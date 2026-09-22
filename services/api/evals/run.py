"""The eval harness.

Runs the real pipeline over the labeled dataset and scores it. This is the
thing that turns "the demo worked" into a number, and it is the number the
README quotes.

    python -m evals.run                    # full dataset
    python -m evals.run --limit 20         # a spread-sampled subset
    python -m evals.run --tags adversarial # guardrails only
    python -m evals.run --no-cache         # force fresh API calls
    python -m evals.run --dry-run          # cost estimate, no API calls

Cost control: LLM responses are cached on disk by content hash, so the first
run pays and every subsequent run while iterating on metrics is free. The
harness prints an estimate and, above --max-spend, refuses to start.

Each case runs in its own transaction which is rolled back afterwards. The
eval never leaves job requests, quotes, or approvals behind; only the EvalRun
and its EvalResult rows are committed.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.table import Table

from agent.llm import CachingLLMClient, LLMClient, build_llm_client
from agent.loop import PipelineResult, run_pipeline
from app.config import API_ROOT, settings
from db.models import EvalResult, EvalRun, JobRequest
from db.session import SessionLocal, engine, session_scope
from evals.dataset import EvalCase, load_cases
from evals.metrics.extraction import ExtractionScorer
from evals.metrics.outcomes import (
    CustomerMatchScorer,
    DraftQualityScorer,
    GuardrailScorer,
    PerformanceScorer,
    QuoteCorrectnessScorer,
)
from observability.cost import PRICING
from scripts.seed import seed_catalog, seed_customers

THRESHOLDS_PATH = API_ROOT / "evals" / "thresholds.yaml"
RESULTS_DIR = API_ROOT / "evals" / "results"

# Rough per-case token shape, measured from the golden-path run. Used only for
# the pre-flight estimate, never for reporting.
EST_TOKENS_IN = 5_200
EST_TOKENS_OUT = 1_400

console = Console()


@dataclass
class CaseOutcome:
    case_id: str
    tags: list[str]
    passed: bool
    scores: dict[str, Any] = field(default_factory=dict)
    diffs: dict[str, Any] = field(default_factory=dict)
    cost_microcents: int = 0
    latency_ms: int = 0


def load_thresholds() -> dict[str, float]:
    return yaml.safe_load(THRESHOLDS_PATH.read_text(encoding="utf-8"))


def estimate_cost_dollars(n_cases: int, model: str) -> float:
    p = PRICING.get(model)
    if p is None:
        return 0.0
    per_case = (
        float(p.input_per_mtok) * EST_TOKENS_IN / 1_000_000
        + float(p.output_per_mtok) * EST_TOKENS_OUT / 1_000_000
    )
    return per_case * n_cases


# ---------------------------------------------------------------------------


def _ensure_reference_data(db) -> None:
    """The pipeline needs the catalog and customer list to exist."""
    from db.models import Customer, ServiceCatalogItem

    if db.query(ServiceCatalogItem).count() == 0:
        seed_catalog(db)
    if db.query(Customer).count() == 0:
        seed_customers(db)
    db.flush()


def run_one(case: EvalCase, client: LLMClient) -> PipelineResult:
    """Run one case in a transaction that is always rolled back.

    The eval exercises the real pipeline, including its database writes, but
    must not accumulate hundreds of quotes and approvals in the demo database.
    """
    connection = engine.connect()
    transaction = connection.begin()
    db = SessionLocal(bind=connection, join_transaction_mode="create_savepoint")
    try:
        _ensure_reference_data(db)
        jr = JobRequest(
            raw_source_text=case.email_body,
            subject=case.email_subject,
            sender_email=case.sender_email,
            received_at=datetime.fromisoformat(case.received_at),
            fixture_case_id=case.ground_truth.case_id,
        )
        db.add(jr)
        db.flush()
        result = run_pipeline(db, job_request=jr, client=client, persist_traces=False)

        # The scorers read these after this session is gone. Touch them now so
        # they are loaded rather than lazy, or scoring dies on a
        # DetachedInstanceError for every case that produced a quote.
        if result.quote is not None:
            _ = list(result.quote.draft_messages)
            _ = list(result.quote.line_items)
        return result
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def evaluate(cases: list[EvalCase], client: LLMClient, *, verbose: bool = True) -> tuple[dict, list[CaseOutcome]]:
    extraction = ExtractionScorer()
    matching = CustomerMatchScorer()
    quotes = QuoteCorrectnessScorer()
    guardrails = GuardrailScorer()
    drafts = DraftQualityScorer()
    perf = PerformanceScorer()

    outcomes: list[CaseOutcome] = []

    for i, case in enumerate(cases, 1):
        gt = case.ground_truth
        diffs: dict[str, Any] = {}
        scores: dict[str, Any] = {}

        try:
            result = run_one(case, client)
        except Exception as exc:  # noqa: BLE001
            outcomes.append(
                CaseOutcome(
                    case_id=gt.case_id,
                    tags=gt.tags,
                    passed=False,
                    diffs={"pipeline_error": f"{type(exc).__name__}: {exc}"},
                )
            )
            if verbose:
                console.print(f"  [red]x[/] {gt.case_id} pipeline error: {exc}")
            continue

        perf.score_case(result)

        # Guardrails are scored on every case: a normal case that gets routed
        # is over-routing, which is a real regression even though nothing unsafe
        # happened.
        guard_ok, guard_note = guardrails.score_case(result, gt)
        scores["guardrail"] = guard_ok
        if guard_note:
            diffs["guardrail"] = guard_note

        # Extraction and matching are only meaningful when the pipeline got far
        # enough to produce them.
        field_results = extraction.score_case(result.parsed, gt)
        scores["extraction"] = {k: v for k, v in field_results.items() if v is not None}
        for name, ok in field_results.items():
            if ok is False:
                diffs[f"extraction.{name}"] = _extraction_diff(name, result, gt)

        if result.parsed is not None:
            match_ok, match_note = matching.score_case(result, gt)
            scores["customer_match"] = match_ok
            if match_note:
                diffs["customer_match"] = match_note

        if gt.expect_quote:
            quote_ok, quote_note = quotes.score_case(result, gt)
            scores["quote_correctness"] = quote_ok
            if quote_note:
                diffs["quote_correctness"] = quote_note

            draft_ok, draft_note = drafts.score_case(result, gt)
            scores["draft_quality"] = draft_ok
            if draft_note:
                diffs["draft_quality"] = draft_note

        # A case passes only if nothing was recorded against it. Extraction
        # misses count: a quote built on a wrong address is not a pass.
        passed = len(diffs) == 0

        outcomes.append(
            CaseOutcome(
                case_id=gt.case_id,
                tags=gt.tags,
                passed=passed,
                scores=scores,
                diffs=diffs,
                cost_microcents=result.run.cost_microcents,
                latency_ms=result.run.latency_ms,
            )
        )

        if verbose and (i % 10 == 0 or i == len(cases)):
            console.print(f"  scored {i}/{len(cases)}")

    metrics = {
        "extraction": extraction.summary(),
        "customer_match": matching.summary(),
        "quote_correctness": quotes.summary(),
        "guardrails": guardrails.summary(),
        "draft_quality": drafts.summary(),
        "performance": perf.summary(),
    }
    return metrics, outcomes


def _extraction_diff(field_name: str, result: PipelineResult, gt) -> str:
    p = result.parsed
    if p is None:
        return "no parse"
    if field_name == "service_types":
        got = sorted({s.catalog_code for s in p.services_requested})
        return f"got {got}, expected {sorted(gt.expected_catalog_codes)}"
    if field_name == "property_size_sqft":
        return f"got {p.property_size_sqft}, expected {gt.property_size_sqft}"
    if field_name == "property_address":
        return f"got {p.property_address!r}, expected {gt.property_address!r}"
    if field_name == "special_requests":
        return f"got {p.special_requests}, expected {gt.special_requests}"
    if field_name == "urgency":
        return f"got {p.urgency}, expected {gt.urgency}"
    return "mismatch"


# ---------------------------------------------------------------------------
# Threshold checking and reporting
# ---------------------------------------------------------------------------


def _get(metrics: dict, path: str) -> Any:
    node: Any = metrics
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def check_thresholds(metrics: dict, thresholds: dict[str, float]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for key, threshold in thresholds.items():
        if key == "extraction.per_field":
            continue  # a display bar, not a gate

        if key.endswith("_max"):
            path = key[: -len("_max")]
            value = _get(metrics, path)
            passed = value is not None and value <= threshold
            rows.append(
                {"metric": path, "value": value, "threshold": threshold,
                 "passed": passed, "direction": "max"}
            )
        else:
            value = _get(metrics, key)
            passed = value is not None and value >= threshold
            rows.append(
                {"metric": key, "value": value, "threshold": threshold,
                 "passed": passed, "direction": "min"}
            )

    return rows


def print_report(metrics: dict, rows: list[dict[str, Any]], outcomes: list[CaseOutcome], model: str) -> bool:
    perf = metrics["performance"]

    table = Table(title="Eval results", title_justify="left", header_style="bold")
    table.add_column("Metric")
    table.add_column("Result", justify="right")
    table.add_column("Threshold", justify="right")
    table.add_column("", justify="center")

    for row in rows:
        value = row["value"]
        is_rate = row["direction"] == "min"
        shown = f"{value:.1%}" if is_rate and isinstance(value, float) else str(value)
        th = f"{row['threshold']:.1%}" if is_rate else str(row["threshold"])
        mark = "[green]PASS[/]" if row["passed"] else "[red]FAIL[/]"
        table.add_row(row["metric"], shown, th, mark)

    console.print()
    console.print(table)

    fields = Table(title="Extraction by field", title_justify="left", header_style="bold")
    fields.add_column("Field")
    fields.add_column("Accuracy", justify="right")
    fields.add_column("n", justify="right")
    counts = metrics["extraction"]["counts"]
    for name, accuracy in metrics["extraction"]["fields"].items():
        n = counts[name]["total"]
        fields.add_row(name, f"{accuracy:.1%}" if n else "—", str(n))
    console.print()
    console.print(fields)

    perf_table = Table(title="Cost and latency", title_justify="left", header_style="bold")
    perf_table.add_column("Measure")
    perf_table.add_column("Value", justify="right")
    perf_table.add_row("model", model)
    n_unc = perf["n_uncached_runs"]
    perf_table.add_row("cache hit rate", f"{perf['cache_hit_rate']:.0%}")
    perf_table.add_row("total run cost (actual spend)", perf["total_cost_display"])
    perf_table.add_section()
    perf_table.add_row(f"[bold]uncached runs[/] (n={n_unc})", "")
    perf_table.add_row("  mean tokens in", f"{perf['uncached_mean_tokens_in']:,}")
    perf_table.add_row("  mean tokens out", f"{perf['uncached_mean_tokens_out']:,}")
    perf_table.add_row("  [bold]cost per quote[/]", perf["uncached_mean_cost_display"])
    perf_table.add_row("  p50 latency", f"{perf['uncached_p50_latency_ms']:,} ms")
    perf_table.add_row("  p95 latency", f"{perf['uncached_p95_latency_ms']:,} ms")
    if n_unc == 0:
        perf_table.add_row("[yellow]note[/]", "fully cached run — cost is not meaningful")
    console.print()
    console.print(perf_table)

    failures = [o for o in outcomes if not o.passed]
    if failures:
        console.print(f"\n[bold]Failing cases[/] ({len(failures)} of {len(outcomes)}):")
        for o in failures[:15]:
            first = next(iter(o.diffs.items()), ("", ""))
            console.print(f"  [yellow]{o.case_id}[/] {first[0]}: {first[1]}")
        if len(failures) > 15:
            console.print(f"  ... and {len(failures) - 15} more")

    passed = all(r["passed"] for r in rows)
    console.print()
    if passed:
        console.print("[bold green]PASS[/] — every threshold met.")
    else:
        failed = [r["metric"] for r in rows if not r["passed"]]
        console.print(f"[bold red]FAIL[/] — below threshold: {', '.join(failed)}")
    return passed


def write_results(
    metrics: dict, rows: list, outcomes: list[CaseOutcome], *, model: str,
    passed: bool, subset: str | None, started: datetime,
) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "started_at": started.isoformat(),
        "ended_at": datetime.now(UTC).isoformat(),
        "model": model,
        "subset": subset,
        "n_cases": len(outcomes),
        "passed": passed,
        "metrics": metrics,
        "threshold_checks": rows,
        "cases": [
            {
                "case_id": o.case_id,
                "tags": o.tags,
                "passed": o.passed,
                "scores": o.scores,
                "diffs": o.diffs,
                "cost_microcents": o.cost_microcents,
                "latency_ms": o.latency_ms,
            }
            for o in outcomes
        ],
    }

    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"eval-{stamp}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (RESULTS_DIR / "latest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def persist_run(
    metrics: dict, thresholds: dict, outcomes: list[CaseOutcome], *,
    model: str, passed: bool, subset: str | None, started: datetime,
) -> None:
    """Store the run so the dashboard can show it."""
    with session_scope() as db:
        run = EvalRun(
            started_at=started,
            ended_at=datetime.now(UTC),
            model=model,
            n_cases=len(outcomes),
            subset=subset,
            metrics=metrics,
            thresholds=thresholds,
            passed=passed,
            total_cost_microcents=sum(o.cost_microcents for o in outcomes),
        )
        db.add(run)
        db.flush()
        for o in outcomes:
            db.add(
                EvalResult(
                    eval_run_id=run.id,
                    case_id=o.case_id,
                    tags=o.tags,
                    passed=o.passed,
                    scores=o.scores,
                    diffs=o.diffs,
                    cost_microcents=o.cost_microcents,
                    latency_ms=o.latency_ms,
                )
            )


# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="score a spread-sampled subset")
    ap.add_argument("--tags", type=str, default=None, help="comma-separated tag filter")
    ap.add_argument("--no-cache", action="store_true", help="bypass the LLM response cache")
    ap.add_argument("--no-adversarial", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="estimate cost and exit")
    ap.add_argument(
        "--max-spend",
        type=float,
        default=15.0,
        help="refuse to start if the estimate exceeds this many dollars",
    )
    ap.add_argument("--no-persist", action="store_true", help="do not write an EvalRun row")
    args = ap.parse_args()

    tags = set(args.tags.split(",")) if args.tags else None
    cases = load_cases(
        include_adversarial=not args.no_adversarial, tags=tags, limit=args.limit
    )

    if not cases:
        console.print(
            "[red]No cases loaded.[/] Generate the dataset first:\n"
            "  python -m scripts.generate_dataset --count 180"
        )
        return 1

    model = settings.llm_model
    estimate = estimate_cost_dollars(len(cases), model)
    subset = args.tags or (f"limit-{args.limit}" if args.limit else None)

    console.print(f"[bold]Eval[/] — {len(cases)} cases on {model}")
    console.print(f"  worst-case estimate: [yellow]${estimate:.2f}[/] (cache hits cost nothing)")

    if args.dry_run:
        console.print("\nDry run — no API calls made.")
        return 0

    if estimate > args.max_spend:
        console.print(
            f"\n[red]Refusing to start.[/] The estimate ${estimate:.2f} exceeds "
            f"--max-spend ${args.max_spend:.2f}. Raise the cap or use --limit."
        )
        return 2

    try:
        client = build_llm_client(use_cache=not args.no_cache)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Cannot build an LLM client:[/] {exc}")
        return 3

    started = datetime.now(UTC)
    thresholds = load_thresholds()

    try:
        metrics, outcomes = evaluate(cases, client)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/] No results written.")
        return 130
    except Exception:
        console.print("[red]Eval crashed:[/]")
        traceback.print_exc()
        return 4

    if isinstance(client, CachingLLMClient):
        console.print(
            f"\n  cache: {client.hits} hits, {client.misses} misses "
            f"({client.hits / max(1, client.hits + client.misses):.0%} free)"
        )

    rows = check_thresholds(metrics, thresholds)
    passed = print_report(metrics, rows, outcomes, model)

    path = write_results(
        metrics, rows, outcomes, model=model, passed=passed, subset=subset, started=started
    )
    console.print(f"\nWrote {path.relative_to(API_ROOT)}")

    if not args.no_persist:
        try:
            persist_run(
                metrics, thresholds, outcomes,
                model=model, passed=passed, subset=subset, started=started,
            )
            console.print("Recorded an EvalRun row; the dashboard will show it.")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Could not persist the eval run:[/] {exc}")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

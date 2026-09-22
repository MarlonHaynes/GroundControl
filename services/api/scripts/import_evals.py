"""Load saved eval result JSON into the database.

`make seed` clears the database, which includes the EvalRun rows the dashboard
reads — so after a re-seed the Eval Dashboard goes blank even though the run
happened and its results are on disk. Re-running the eval to get them back
would cost real money for data we already have.

This replays `evals/results/*.json` into EvalRun/EvalResult instead. No API
calls, no pipeline execution, just the recorded numbers.

    python -m scripts.import_evals            # every result file
    python -m scripts.import_evals --latest   # just the most recent
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.config import API_ROOT
from db.models import EvalResult, EvalRun
from db.session import session_scope

RESULTS_DIR = API_ROOT / "evals" / "results"


def _thresholds_from(payload: dict) -> dict:
    """Result files store threshold checks as rows; the model wants a mapping."""
    return {
        row["metric"] if row["direction"] == "min" else f"{row['metric']}_max": row["threshold"]
        for row in payload.get("threshold_checks", [])
    }


def import_file(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    started = datetime.fromisoformat(payload["started_at"])

    with session_scope() as db:
        existing = db.execute(
            select(EvalRun).where(EvalRun.started_at == started)
        ).scalar_one_or_none()
        if existing is not None:
            return f"  {path.name}: already present, skipped"

        run = EvalRun(
            started_at=started,
            ended_at=(
                datetime.fromisoformat(payload["ended_at"]) if payload.get("ended_at") else None
            ),
            model=payload["model"],
            n_cases=payload["n_cases"],
            subset=payload.get("subset"),
            metrics=payload["metrics"],
            thresholds=_thresholds_from(payload),
            passed=payload["passed"],
            total_cost_microcents=sum(c.get("cost_microcents", 0) for c in payload["cases"]),
            notes=f"imported from {path.name}",
        )
        db.add(run)
        db.flush()

        for case in payload["cases"]:
            db.add(
                EvalResult(
                    eval_run_id=run.id,
                    case_id=case["case_id"],
                    tags=case.get("tags", []),
                    passed=case["passed"],
                    scores=case.get("scores", {}),
                    diffs=case.get("diffs", {}),
                    cost_microcents=case.get("cost_microcents", 0),
                    latency_ms=case.get("latency_ms", 0),
                )
            )

    return f"  {path.name}: {payload['n_cases']} cases, passed={payload['passed']}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--latest", action="store_true", help="import only the newest result file")
    args = ap.parse_args()

    files = sorted(
        (p for p in RESULTS_DIR.glob("eval-*.json")), key=lambda p: p.stat().st_mtime
    )
    if not files:
        print("No result files in evals/results/. Run `make eval-full` first.")
        return
    if args.latest:
        files = files[-1:]

    print(f"Importing {len(files)} result file(s)...")
    for path in files:
        print(import_file(path))
    print("Done.")


if __name__ == "__main__":
    main()

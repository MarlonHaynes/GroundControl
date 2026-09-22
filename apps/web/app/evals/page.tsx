import { BarChart3, CheckCircle2, XCircle } from "lucide-react";

import { CostDistributionChart, FieldAccuracyChart } from "@/components/eval-charts";
import { EmptyState, Metric } from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api, type EvalRunDetail } from "@/lib/api";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";

const pct = (v: unknown) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "—");
const num = (v: unknown) => (typeof v === "number" ? v.toLocaleString() : "—");

/** The headline rows, in the order a reviewer should read them. */
function summaryRows(run: EvalRunDetail) {
  const m = run.metrics as Record<string, Record<string, unknown>>;
  const t = run.thresholds as Record<string, number>;

  const rows: {
    metric: string;
    value: string;
    threshold: string;
    passed: boolean | null;
    detail: string;
  }[] = [];

  const add = (
    metric: string,
    raw: unknown,
    thresholdKey: string,
    detail: string,
    fmt: (v: unknown) => string = pct,
    higherIsBetter = true
  ) => {
    const th = t[thresholdKey];
    const passed =
      typeof raw === "number" && typeof th === "number"
        ? higherIsBetter
          ? raw >= th
          : raw <= th
        : null;
    rows.push({
      metric,
      value: fmt(raw),
      threshold: typeof th === "number" ? fmt(th) : "—",
      passed,
      detail,
    });
  };

  add(
    "Extraction accuracy",
    m.extraction?.aggregate,
    "extraction.aggregate",
    "field-level match against ground truth"
  );
  add(
    "Customer-match accuracy",
    m.customer_match?.accuracy,
    "customer_match.accuracy",
    `${num(m.customer_match?.correct_link)} linked, ${num(m.customer_match?.correct_new)} correctly new`
  );
  add(
    "Quote correctness",
    m.quote_correctness?.in_band_rate,
    "quote_correctness.in_band_rate",
    `within ${pct(m.quote_correctness?.tolerance)} of expected · ${num(m.quote_correctness?.out_of_band)} out of band`
  );
  add(
    "Guardrail pass rate",
    m.guardrails?.pass_rate,
    "guardrails.pass_rate",
    `${num(m.guardrails?.n_adversarial)} adversarial cases`
  );
  add(
    "Hallucinated quotes",
    m.guardrails?.hallucinated_quotes,
    "guardrails.hallucinated_quotes_max",
    "quotes produced on a case that should have been routed — a hard failure",
    num,
    false
  );
  add(
    "Draft quality",
    m.draft_quality?.pass_rate,
    "draft_quality.pass_rate",
    "no invented figures, quote number present, totals reconcile"
  );

  return rows;
}

export default async function EvalsPage() {
  const run = await api.latestEval();

  if (!run) {
    return (
      <div className="space-y-6">
        <Header />
        <EmptyState
          icon={BarChart3}
          title="No eval runs recorded"
          hint="Run `make eval` for a 20-case subset, or `make eval-full` for the whole dataset. Results are written to the database and to services/api/evals/results/."
        />
      </div>
    );
  }

  const rows = summaryRows(run);
  const m = run.metrics as Record<string, Record<string, unknown>>;
  const perf = m.performance ?? {};

  const fieldData = Object.entries((m.extraction?.fields as Record<string, number>) ?? {}).map(
    ([field, accuracy]) => ({
      field: field.replace(/_/g, " "),
      accuracy,
      threshold: (run.thresholds as Record<string, number>)["extraction.per_field"] ?? 0.85,
    })
  );

  const costData = bucketCosts(run);

  return (
    <div className="space-y-6">
      <Header />

      <div
        className={cn(
          "flex flex-wrap items-center justify-between gap-4 rounded-lg border p-4",
          run.passed
            ? "border-emerald-500/30 bg-emerald-50/50 dark:bg-emerald-500/5"
            : "border-amber-500/30 bg-amber-50/50 dark:bg-amber-500/5"
        )}
      >
        <div className="flex items-center gap-3">
          {run.passed ? (
            <CheckCircle2 className="size-5 text-emerald-600" />
          ) : (
            <XCircle className="size-5 text-amber-600" />
          )}
          <div>
            <div className="text-[14px] font-semibold">
              {run.passed ? "All thresholds met" : "Below threshold"}
            </div>
            <div className="text-muted-foreground text-[12px]">
              {run.n_cases} cases · {run.model}
              {run.subset && <> · {run.subset}</>} · {new Date(run.started_at).toLocaleString()}
            </div>
          </div>
        </div>
        <div className="text-right">
          <div className="text-lg font-semibold tabular-nums">{run.cost_display}</div>
          <div className="text-muted-foreground text-[11px]">total run cost</div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Metric
          label="Cost per quote"
          value={
            typeof perf.mean_cost_display === "string" ? (perf.mean_cost_display as string) : "—"
          }
        />
        <Metric label="Mean tokens in" value={num(perf.mean_tokens_in)} />
        <Metric label="Mean tokens out" value={num(perf.mean_tokens_out)} />
        <Metric
          label="Latency p50 / p95"
          value={`${num(perf.p50_latency_ms)} / ${num(perf.p95_latency_ms)} ms`}
        />
      </div>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="w-[26%]">Metric</TableHead>
              <TableHead className="text-right">Result</TableHead>
              <TableHead className="text-right">Threshold</TableHead>
              <TableHead className="w-24 text-center">Status</TableHead>
              <TableHead>Detail</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.metric}>
                <TableCell className="text-[13px] font-medium">{r.metric}</TableCell>
                <TableCell className="text-right text-[13px] font-semibold tabular-nums">
                  {r.value}
                </TableCell>
                <TableCell className="text-muted-foreground text-right text-[12px] tabular-nums">
                  {r.threshold}
                </TableCell>
                <TableCell className="text-center">
                  {r.passed === null ? (
                    <span className="text-muted-foreground text-[12px]">—</span>
                  ) : (
                    <Badge
                      variant="outline"
                      className={cn(
                        "text-[10px] font-medium",
                        r.passed
                          ? "border-emerald-500/40 text-emerald-700 dark:text-emerald-400"
                          : "border-amber-500/40 text-amber-700 dark:text-amber-400"
                      )}
                    >
                      {r.passed ? "PASS" : "FAIL"}
                    </Badge>
                  )}
                </TableCell>
                <TableCell className="text-muted-foreground text-[12px]">{r.detail}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-lg border p-4">
          <h2 className="mb-1 text-[13px] font-semibold">Extraction accuracy by field</h2>
          <p className="text-muted-foreground mb-3 text-[11px]">
            Green meets the per-field threshold; amber does not.
          </p>
          <FieldAccuracyChart data={fieldData} />
        </div>

        <div className="rounded-lg border p-4">
          <h2 className="mb-1 text-[13px] font-semibold">Cost per case</h2>
          <p className="text-muted-foreground mb-3 text-[11px]">
            Distribution across the {run.results.length} scored cases.
          </p>
          <CostDistributionChart data={costData} />
        </div>
      </div>

      <FailureList run={run} />
    </div>
  );
}

function Header() {
  return (
    <div>
      <h1 className="text-xl font-semibold tracking-tight">Eval dashboard</h1>
      <p className="text-muted-foreground mt-0.5 text-[13px]">
        The latest run of the eval harness against the labeled dataset.
      </p>
    </div>
  );
}

function bucketCosts(run: EvalRunDetail) {
  const costs = run.results.map((r) => r.cost_microcents).filter((c) => c > 0);
  if (costs.length === 0) return [];

  const max = Math.max(...costs);
  const buckets = 8;
  const width = Math.max(1, Math.ceil(max / buckets));
  const counts = new Array(buckets).fill(0);

  for (const c of costs) {
    counts[Math.min(buckets - 1, Math.floor(c / width))] += 1;
  }

  return counts.map((count, i) => ({
    bucket: `${((i * width) / 100000).toFixed(2)}`,
    count,
  }));
}

function FailureList({ run }: { run: EvalRunDetail }) {
  const failures = run.results.filter((r) => !r.passed).slice(0, 25);
  if (failures.length === 0) {
    return (
      <div className="rounded-lg border border-emerald-500/30 bg-emerald-50/40 p-4 text-[13px] dark:bg-emerald-500/5">
        Every scored case passed.
      </div>
    );
  }

  return (
    <div className="rounded-lg border">
      <div className="border-b px-4 py-3">
        <h2 className="text-[13px] font-semibold">
          Failing cases ({run.results.filter((r) => !r.passed).length})
        </h2>
        <p className="text-muted-foreground mt-0.5 text-[11px]">
          The cases worth reading before changing a prompt.
        </p>
      </div>
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="w-32">Case</TableHead>
            <TableHead className="w-48">Tags</TableHead>
            <TableHead>What went wrong</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {failures.map((r) => (
            <TableRow key={r.case_id}>
              <TableCell className="font-mono text-[11px]">{r.case_id}</TableCell>
              <TableCell>
                <div className="flex flex-wrap gap-1">
                  {r.tags.slice(0, 3).map((t) => (
                    <Badge key={t} variant="outline" className="text-[9px] font-normal">
                      {t}
                    </Badge>
                  ))}
                </div>
              </TableCell>
              <TableCell className="text-muted-foreground font-mono text-[11px]">
                {Object.entries(r.diffs)
                  .slice(0, 3)
                  .map(([k, v]) => `${k}: ${JSON.stringify(v)}`)
                  .join(" · ") || "—"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

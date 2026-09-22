import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft } from "lucide-react";

import { Metric, StatusPill, formatMs } from "@/components/shared";
import { TraceStepRow } from "@/components/trace-step";
import { api } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function TraceDetailPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;

  let run;
  try {
    run = await api.run(runId);
  } catch {
    notFound();
  }

  const maxLatency = Math.max(...run.steps.map((s) => s.latency_ms), 1);
  const llmSteps = run.steps.filter((s) => s.uses_llm);
  const llmLatency = llmSteps.reduce((sum, s) => sum + s.latency_ms, 0);

  return (
    <div className="space-y-6">
      <div>
        <Link
          href="/traces"
          className="text-muted-foreground hover:text-foreground mb-2 inline-flex items-center gap-1 text-[12px]"
        >
          <ArrowLeft className="size-3" /> All traces
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold tracking-tight">
            {run.job_request_subject || "(no subject)"}
          </h1>
          <StatusPill value={run.status} kind="run" />
        </div>
        <p className="text-muted-foreground mt-1 font-mono text-[11px]">
          {run.id} · {run.model}
          {run.outcome && <> · {run.outcome.replace(/_/g, " ")}</>}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Metric label="Steps" value={run.steps.length} sub={`${llmSteps.length} LLM calls`} />
        <Metric label="Tokens in" value={run.total_tokens_in.toLocaleString()} />
        <Metric label="Tokens out" value={run.total_tokens_out.toLocaleString()} />
        <Metric label="Cost" value={run.cost_display} />
        <Metric
          label="Latency"
          value={formatMs(run.latency_ms)}
          sub={`${formatMs(llmLatency)} in the model`}
        />
      </div>

      {run.error && (
        <div className="rounded-lg border border-red-500/30 bg-red-50/60 p-4 dark:bg-red-500/5">
          <div className="text-[12px] font-medium text-red-900 dark:text-red-300">Run failed</div>
          <pre className="mt-1 font-mono text-[11px] whitespace-pre-wrap text-red-900/80 dark:text-red-200/80">
            {run.error}
          </pre>
        </div>
      )}

      <div className="overflow-hidden rounded-lg border">
        <div className="text-muted-foreground bg-muted/30 flex items-center gap-3 border-b px-4 py-2 text-[10px] font-semibold tracking-wider uppercase">
          <span className="w-3.5" />
          <span className="w-5 text-right">#</span>
          <span className="w-52">Tool</span>
          <span className="flex-1">Duration</span>
          <span className="w-24 text-right">Tokens</span>
          <span className="w-20 text-right">Cost</span>
          <span className="w-16 text-right">Time</span>
        </div>
        {run.steps.map((step) => (
          <TraceStepRow key={step.id} step={step} maxLatency={maxLatency} />
        ))}
      </div>

      <p className="text-muted-foreground text-[11px]">
        Violet bars are LLM calls; grey bars are deterministic code. Click any step to see its exact
        input and output.
      </p>
    </div>
  );
}

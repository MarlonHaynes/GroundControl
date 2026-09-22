import Link from "next/link";
import { Activity } from "lucide-react";

import { EmptyState, Metric, StatusPill, formatMs, relativeTime } from "@/components/shared";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function TracesPage() {
  const [runs, stats] = await Promise.all([api.runs(200), api.stats()]);

  const withLatency = runs.filter((r) => r.latency_ms != null);
  const sorted = [...withLatency].sort((a, b) => (a.latency_ms ?? 0) - (b.latency_ms ?? 0));
  const p50 = sorted.length ? sorted[Math.floor(sorted.length * 0.5)].latency_ms : null;
  const p95 = sorted.length ? sorted[Math.floor(sorted.length * 0.95)].latency_ms : null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Traces</h1>
        <p className="text-muted-foreground mt-0.5 text-[13px]">
          Every pipeline run, step by step, with what each one cost.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Metric label="Runs" value={runs.length} />
        <Metric label="Total spend" value={stats?.total_spend_display ?? "$0.00"} />
        <Metric label="Mean per run" value={stats?.mean_cost_per_run_display ?? "$0.00"} />
        <Metric label="Latency" value={formatMs(p50)} sub={`p95 ${formatMs(p95)}`} />
      </div>

      {runs.length === 0 ? (
        <EmptyState
          icon={Activity}
          title="No runs recorded"
          hint="Each pipeline execution is traced here with its tool calls, tokens, latency, and cost."
        />
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-[32%]">Run</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Outcome</TableHead>
                <TableHead className="text-right">Steps</TableHead>
                <TableHead className="text-right">Tokens</TableHead>
                <TableHead className="text-right">Cost</TableHead>
                <TableHead className="text-right">Latency</TableHead>
                <TableHead className="text-right">When</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.map((r) => (
                <TableRow key={r.id} className="group">
                  <TableCell className="max-w-0">
                    <Link href={`/traces/${r.id}`} className="block">
                      <div className="truncate text-[13px] group-hover:underline">
                        {r.job_request_subject || "(no subject)"}
                      </div>
                      <div className="text-muted-foreground truncate font-mono text-[10px]">
                        {r.id.slice(0, 8)} · {r.model}
                      </div>
                    </Link>
                  </TableCell>
                  <TableCell>
                    <StatusPill value={r.status} kind="run" />
                  </TableCell>
                  <TableCell className="text-muted-foreground text-[12px]">
                    {r.outcome?.replace(/_/g, " ") ?? "—"}
                  </TableCell>
                  <TableCell className="text-right text-[12px] tabular-nums">{r.n_steps}</TableCell>
                  <TableCell className="text-muted-foreground text-right text-[11px] tabular-nums">
                    {r.total_tokens_in.toLocaleString()} / {r.total_tokens_out.toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right text-[12px] font-medium tabular-nums">
                    {r.cost_display}
                  </TableCell>
                  <TableCell className="text-right text-[12px] tabular-nums">
                    {formatMs(r.latency_ms)}
                  </TableCell>
                  <TableCell className="text-muted-foreground text-right text-[12px] whitespace-nowrap">
                    {relativeTime(r.started_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}

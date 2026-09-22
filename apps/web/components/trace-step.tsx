"use client";

import { useState } from "react";
import { AlertCircle, ChevronRight, Cpu, Database, Zap } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { formatMs } from "@/components/shared";
import { cn } from "@/lib/utils";
import type { TraceStepOut } from "@/lib/api";

export function TraceStepRow({ step, maxLatency }: { step: TraceStepOut; maxLatency: number }) {
  const [open, setOpen] = useState(false);
  const failed = step.status === "error";

  // The waterfall bar is proportional to the slowest step in this run, which
  // makes the LLM calls visually obvious against the deterministic ones.
  const widthPct = maxLatency > 0 ? Math.max(1.5, (step.latency_ms / maxLatency) * 100) : 0;

  return (
    <div className={cn("border-b last:border-b-0", failed && "bg-red-50/50 dark:bg-red-500/5")}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="hover:bg-muted/40 flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors"
      >
        <ChevronRight
          className={cn(
            "text-muted-foreground size-3.5 shrink-0 transition-transform",
            open && "rotate-90"
          )}
        />

        <span className="text-muted-foreground w-5 shrink-0 text-right font-mono text-[11px]">
          {step.seq}
        </span>

        <span className="flex w-52 shrink-0 items-center gap-1.5">
          {failed ? (
            <AlertCircle className="size-3.5 shrink-0 text-red-600" />
          ) : step.uses_llm ? (
            <Cpu className="size-3.5 shrink-0 text-violet-500" />
          ) : (
            <Database className="text-muted-foreground/60 size-3.5 shrink-0" />
          )}
          <span className="truncate font-mono text-[12px]">{step.tool_name}</span>
        </span>

        <span className="min-w-0 flex-1">
          <span className="bg-muted/60 block h-1.5 overflow-hidden rounded-full">
            <span
              className={cn(
                "block h-full rounded-full",
                failed ? "bg-red-500" : step.uses_llm ? "bg-violet-500" : "bg-muted-foreground/40"
              )}
              style={{ width: `${widthPct}%` }}
            />
          </span>
        </span>

        {step.cache_hit && (
          <Badge variant="outline" className="h-4 shrink-0 gap-0.5 px-1 text-[9px] font-normal">
            <Zap className="size-2" /> cached
          </Badge>
        )}

        <span className="text-muted-foreground w-24 shrink-0 text-right text-[11px] tabular-nums">
          {step.uses_llm ? `${step.tokens_in}/${step.tokens_out}` : "—"}
        </span>
        <span className="w-20 shrink-0 text-right text-[11px] tabular-nums">
          {step.cost_microcents > 0 ? step.cost_display : "—"}
        </span>
        <span className="w-16 shrink-0 text-right text-[11px] tabular-nums">
          {formatMs(step.latency_ms)}
        </span>
      </button>

      {open && (
        <div className="grid gap-3 px-4 pb-4 pl-14 md:grid-cols-2">
          <Payload title="Input" value={step.input} />
          <Payload title="Output" value={step.output} />
          {step.error && (
            <div className="md:col-span-2">
              <div className="mb-1 text-[10px] font-semibold tracking-wider text-red-600 uppercase">
                Error
              </div>
              <pre className="overflow-auto rounded-md bg-red-50 p-3 font-mono text-[11px] text-red-900 dark:bg-red-500/10 dark:text-red-300">
                {step.error}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Payload({ title, value }: { title: string; value: unknown }) {
  const text = JSON.stringify(value, null, 2);
  const empty = !value || text === "{}";
  return (
    <div className="min-w-0">
      <div className="text-muted-foreground mb-1 text-[10px] font-semibold tracking-wider uppercase">
        {title}
      </div>
      <pre className="bg-muted/40 max-h-80 overflow-auto rounded-md p-3 font-mono text-[11px] leading-relaxed">
        {empty ? "—" : text}
      </pre>
    </div>
  );
}

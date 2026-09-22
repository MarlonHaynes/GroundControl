import { AlertTriangle, UserPlus } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { JobRequestStatus, QuoteStatus, RunStatus } from "@/lib/api";

/** Status pill colours are semantic, not decorative: amber always means
 *  "a human needs to look at this". */
const QUOTE_TONE: Record<string, string> = {
  draft: "bg-muted text-muted-foreground",
  pending_approval: "bg-amber-100 text-amber-900 dark:bg-amber-500/15 dark:text-amber-300",
  approved: "bg-blue-100 text-blue-900 dark:bg-blue-500/15 dark:text-blue-300",
  rejected: "bg-muted text-muted-foreground line-through",
  sent: "bg-emerald-100 text-emerald-900 dark:bg-emerald-500/15 dark:text-emerald-300",
};

const REQUEST_TONE: Record<string, string> = {
  received: "bg-muted text-muted-foreground",
  processing: "bg-blue-100 text-blue-900 dark:bg-blue-500/15 dark:text-blue-300",
  quoted: "bg-emerald-100 text-emerald-900 dark:bg-emerald-500/15 dark:text-emerald-300",
  routed_to_human: "bg-amber-100 text-amber-900 dark:bg-amber-500/15 dark:text-amber-300",
  failed: "bg-red-100 text-red-900 dark:bg-red-500/15 dark:text-red-300",
};

const RUN_TONE: Record<string, string> = {
  running: "bg-blue-100 text-blue-900 dark:bg-blue-500/15 dark:text-blue-300",
  succeeded: "bg-emerald-100 text-emerald-900 dark:bg-emerald-500/15 dark:text-emerald-300",
  routed_to_human: "bg-amber-100 text-amber-900 dark:bg-amber-500/15 dark:text-amber-300",
  failed: "bg-red-100 text-red-900 dark:bg-red-500/15 dark:text-red-300",
};

function label(value: string) {
  return value.replace(/_/g, " ");
}

export function StatusPill({
  value,
  kind = "quote",
}: {
  value: QuoteStatus | JobRequestStatus | RunStatus | string;
  kind?: "quote" | "request" | "run";
}) {
  const map = kind === "request" ? REQUEST_TONE : kind === "run" ? RUN_TONE : QUOTE_TONE;
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium whitespace-nowrap capitalize",
        map[value] ?? "bg-muted text-muted-foreground"
      )}
    >
      {label(value)}
    </span>
  );
}

export function ReviewFlags({
  needsReview,
  isNewCustomer,
  reasons,
  compact = false,
}: {
  needsReview: boolean;
  isNewCustomer: boolean;
  reasons: string[];
  compact?: boolean;
}) {
  if (!needsReview && !isNewCustomer) return null;

  if (compact) {
    return (
      <div className="flex flex-wrap items-center gap-1">
        {isNewCustomer && (
          <Badge variant="outline" className="gap-1 text-[10px] font-normal">
            <UserPlus className="size-2.5" /> new
          </Badge>
        )}
        {needsReview && (
          <Badge
            variant="outline"
            className="gap-1 border-amber-500/40 text-[10px] font-normal text-amber-700 dark:text-amber-400"
          >
            <AlertTriangle className="size-2.5" /> review
          </Badge>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-md border border-amber-500/30 bg-amber-50/60 p-3 dark:bg-amber-500/5">
      <div className="flex items-center gap-1.5 text-[12px] font-medium text-amber-900 dark:text-amber-300">
        <AlertTriangle className="size-3.5" />
        Needs a human look
      </div>
      <ul className="mt-1.5 space-y-0.5">
        {reasons.map((r) => (
          <li key={r} className="text-[12px] text-amber-900/80 dark:text-amber-200/80">
            • {r}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function EmptyState({
  title,
  hint,
  icon: Icon,
}: {
  title: string;
  hint?: string;
  icon?: React.ComponentType<{ className?: string }>;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-16 text-center">
      {Icon && <Icon className="text-muted-foreground/50 mb-3 size-7" />}
      <p className="text-[13px] font-medium">{title}</p>
      {hint && <p className="text-muted-foreground mt-1 max-w-sm text-[12px]">{hint}</p>}
    </div>
  );
}

export function Metric({
  label: l,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string | number;
  sub?: string;
  tone?: "default" | "warn" | "good";
}) {
  return (
    <div className="rounded-lg border p-4">
      <div className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
        {l}
      </div>
      <div
        className={cn(
          "mt-1.5 text-2xl font-semibold tabular-nums",
          tone === "warn" && "text-amber-600 dark:text-amber-400",
          tone === "good" && "text-emerald-600 dark:text-emerald-400"
        )}
      >
        {value}
      </div>
      {sub && <div className="text-muted-foreground mt-0.5 text-[11px]">{sub}</div>}
    </div>
  );
}

export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const mins = Math.round((Date.now() - then) / 60000);
  if (!Number.isFinite(mins)) return "";
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function formatMs(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

import Link from "next/link";
import { Inbox as InboxIcon } from "lucide-react";

import { EmptyState, Metric, ReviewFlags, StatusPill, relativeTime } from "@/components/shared";
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

export default async function InboxPage() {
  const [requests, stats] = await Promise.all([api.jobRequests({ limit: 200 }), api.stats()]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Inbox</h1>
        <p className="text-muted-foreground mt-0.5 text-[13px]">
          Every inbound job request and where the pipeline took it.
        </p>
      </div>

      {stats && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <Metric label="Requests" value={stats.total_job_requests} />
          <Metric
            label="Awaiting approval"
            value={stats.pending_approval}
            tone={stats.pending_approval > 0 ? "warn" : "default"}
          />
          <Metric label="Routed to human" value={stats.routed_to_human} sub="no quote produced" />
          <Metric label="Sent" value={stats.sent} tone="good" />
          <Metric
            label="API spend"
            value={stats.total_spend_display}
            sub={`${stats.mean_cost_per_run_display} per run`}
          />
        </div>
      )}

      {requests.length === 0 ? (
        <EmptyState
          icon={InboxIcon}
          title="No job requests yet"
          hint="Run `make seed` to load the demo fixtures, then `--run-pipeline 10` to process some."
        />
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-[30%]">Request</TableHead>
                <TableHead>Customer</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Flags</TableHead>
                <TableHead className="text-right">Quote</TableHead>
                <TableHead className="text-right">Received</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {requests.map((r) => (
                <TableRow key={r.id} className="group">
                  <TableCell className="max-w-0">
                    <Link
                      href={r.latest_run_id ? `/traces/${r.latest_run_id}` : "/inbox"}
                      className="block"
                    >
                      <div className="truncate text-[13px] font-medium group-hover:underline">
                        {r.subject || "(no subject)"}
                      </div>
                      <div className="text-muted-foreground truncate font-mono text-[11px]">
                        {r.sender_email || "unknown sender"}
                      </div>
                      {r.routed_reason && (
                        <div className="mt-1 truncate text-[11px] text-amber-700 dark:text-amber-400">
                          {r.routed_reason}
                        </div>
                      )}
                    </Link>
                  </TableCell>

                  <TableCell className="text-[12px]">
                    {r.customer_name ? (
                      <>
                        <div className="truncate">{r.customer_name}</div>
                        {r.customer_match_score != null && (
                          <div className="text-muted-foreground text-[11px] tabular-nums">
                            match {r.customer_match_score.toFixed(2)}
                          </div>
                        )}
                      </>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </TableCell>

                  <TableCell>
                    <StatusPill value={r.status} kind="request" />
                  </TableCell>

                  <TableCell>
                    <ReviewFlags
                      compact
                      needsReview={r.needs_review}
                      isNewCustomer={r.is_new_customer}
                      reasons={r.review_reasons}
                    />
                  </TableCell>

                  <TableCell className="text-right">
                    {r.quote_total ? (
                      <>
                        <div className="text-[13px] font-medium tabular-nums">
                          {r.quote_total.display}
                        </div>
                        <div className="text-muted-foreground font-mono text-[10px]">
                          {r.quote_number}
                        </div>
                      </>
                    ) : (
                      <span className="text-muted-foreground text-[12px]">—</span>
                    )}
                  </TableCell>

                  <TableCell className="text-muted-foreground text-right text-[12px] whitespace-nowrap">
                    {relativeTime(r.received_at)}
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

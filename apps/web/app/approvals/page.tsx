import { ShieldCheck } from "lucide-react";

import { ApprovalCard } from "@/components/approval-card";
import { EmptyState } from "@/components/shared";
import { api } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function ApprovalsPage() {
  const queue = await api.approvalQueue();

  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Approval queue</h1>
          <p className="text-muted-foreground mt-0.5 text-[13px]">
            Nothing here has been sent. A quote leaves the system only when you approve it.
          </p>
        </div>
        {queue.length > 0 && (
          <div className="text-muted-foreground text-[13px] tabular-nums">
            {queue.length} awaiting review
          </div>
        )}
      </div>

      {queue.length === 0 ? (
        <EmptyState
          icon={ShieldCheck}
          title="Queue is clear"
          hint="Quotes waiting on a human decision appear here. Run `make seed --run-pipeline 10` to populate a demo queue."
        />
      ) : (
        <div className="space-y-5">
          {queue.map((item) => (
            <ApprovalCard key={item.quote.id} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}

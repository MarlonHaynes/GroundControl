"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import Link from "next/link";
import { Check, ChevronDown, Loader2, Mail, Pencil, X } from "lucide-react";
import { toast } from "sonner";

import { ReviewFlags, StatusPill, relativeTime } from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, api, type ApprovalQueueItem } from "@/lib/api";
import { cn } from "@/lib/utils";

const ACTOR = "office@riversidegrounds.com";

type EditRow = { catalog_code: string; quantity: string; trunk_diameter_band?: string | null };

export function ApprovalCard({ item }: { item: ApprovalQueueItem }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [showRaw, setShowRaw] = useState(false);
  const [notes, setNotes] = useState("");
  const [rows, setRows] = useState<EditRow[]>(
    item.quote.line_items.map((li) => ({
      catalog_code: li.catalog_code,
      quantity: String(li.quantity),
      trunk_diameter_band: null,
    }))
  );
  const [draftBody, setDraftBody] = useState(
    item.quote.draft?.edited_body ?? item.quote.draft?.body ?? ""
  );

  const { quote, job_request: jr, customer } = item;

  function refresh() {
    startTransition(() => router.refresh());
  }

  async function act(kind: "approve" | "reject") {
    setBusy(kind);
    try {
      if (kind === "approve") {
        const res = await api.approve(quote.id, ACTOR, notes);
        toast.success(`Sent to ${res.to_email}`, {
          description: `${quote.quote_number} · ${quote.total.display}`,
        });
      } else {
        await api.reject(quote.id, ACTOR, notes);
        toast.success(`Rejected ${quote.quote_number}`);
      }
      refresh();
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : String(e);
      toast.error(kind === "approve" ? "Could not send" : "Could not reject", {
        description: msg,
      });
    } finally {
      setBusy(null);
    }
  }

  async function saveEdit() {
    setBusy("edit");
    try {
      await api.edit(quote.id, {
        line_items: rows.map((r) => ({
          catalog_code: r.catalog_code,
          quantity: r.quantity,
          trunk_diameter_band: r.trunk_diameter_band || null,
        })),
        actor: ACTOR,
        notes: notes || null,
        draft_body: draftBody !== (quote.draft?.body ?? "") ? draftBody : null,
      });
      toast.success("Re-priced", { description: "The quote was recalculated by the engine." });
      setEditing(false);
      refresh();
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : String(e);
      toast.error("Could not apply the edit", { description: msg });
    } finally {
      setBusy(null);
    }
  }

  const disabled = busy !== null || pending;

  return (
    <div className="bg-card overflow-hidden rounded-xl border shadow-sm">
      {/* header */}
      <div className="flex flex-wrap items-start justify-between gap-3 border-b px-5 py-3.5">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-[14px] font-semibold">
              {customer?.name ?? jr.sender_email ?? "Unknown customer"}
            </h2>
            <StatusPill value={quote.status} />
            {jr.is_new_customer && (
              <Badge variant="outline" className="text-[10px] font-normal">
                new customer
              </Badge>
            )}
          </div>
          <div className="text-muted-foreground mt-0.5 flex items-center gap-2 text-[11px]">
            <span className="font-mono">{quote.quote_number}</span>
            <span>·</span>
            <span>{relativeTime(jr.received_at)}</span>
            {jr.customer_match_score != null && (
              <>
                <span>·</span>
                <span className="tabular-nums">match {jr.customer_match_score.toFixed(2)}</span>
              </>
            )}
          </div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-semibold tabular-nums">{quote.total.display}</div>
          <div className="text-muted-foreground text-[11px]">
            {quote.adjusted_subtotal.display} + {quote.tax.display} tax
          </div>
        </div>
      </div>

      {/* three panes: what they asked, what we priced, what we'd send */}
      <div className="grid gap-px lg:grid-cols-3">
        {/* 1. original request */}
        <section className="bg-background p-5">
          <SectionLabel>What they sent</SectionLabel>
          <div className="text-muted-foreground mb-2 truncate text-[12px]">
            {jr.subject || "(no subject)"}
          </div>
          <pre
            className={cn(
              "bg-muted/40 text-foreground/90 overflow-hidden rounded-md p-3 font-sans text-[12px] leading-relaxed whitespace-pre-wrap",
              !showRaw && "max-h-44"
            )}
          >
            {jr.raw_source_text}
          </pre>
          {jr.raw_source_text.length > 400 && (
            <button
              onClick={() => setShowRaw((v) => !v)}
              className="text-muted-foreground hover:text-foreground mt-1.5 flex items-center gap-1 text-[11px]"
            >
              <ChevronDown className={cn("size-3 transition-transform", showRaw && "rotate-180")} />
              {showRaw ? "Show less" : "Show full email"}
            </button>
          )}

          {jr.parsed && <ParsedSummary parsed={jr.parsed} confidence={jr.field_confidence} />}
        </section>

        {/* 2. the priced quote */}
        <section className="bg-background p-5">
          <div className="mb-2 flex items-center justify-between">
            <SectionLabel>What we priced</SectionLabel>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 gap-1 px-2 text-[11px]"
              onClick={() => setEditing((v) => !v)}
              disabled={disabled}
            >
              <Pencil className="size-3" />
              {editing ? "Cancel" : "Edit"}
            </Button>
          </div>

          {editing ? (
            <div className="space-y-2">
              {rows.map((row, i) => (
                <div key={i} className="flex items-center gap-2">
                  <Input
                    value={row.catalog_code}
                    onChange={(e) =>
                      setRows((rs) =>
                        rs.map((r, j) => (j === i ? { ...r, catalog_code: e.target.value } : r))
                      )
                    }
                    className="h-8 font-mono text-[11px]"
                  />
                  <Input
                    value={row.quantity}
                    onChange={(e) =>
                      setRows((rs) =>
                        rs.map((r, j) => (j === i ? { ...r, quantity: e.target.value } : r))
                      )
                    }
                    className="h-8 w-24 text-right text-[12px] tabular-nums"
                  />
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-8 shrink-0"
                    onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}
                    disabled={rows.length === 1}
                  >
                    <X className="size-3.5" />
                  </Button>
                </div>
              ))}
              <Button
                variant="outline"
                size="sm"
                className="h-7 w-full text-[11px]"
                onClick={() =>
                  setRows((rs) => [...rs, { catalog_code: "MOW_STD", quantity: "1000" }])
                }
              >
                Add line
              </Button>
              <p className="text-muted-foreground text-[11px] leading-relaxed">
                Totals are recomputed by the pricing engine, not entered by hand.
              </p>
              <Button size="sm" className="h-8 w-full" onClick={saveEdit} disabled={disabled}>
                {busy === "edit" ? <Loader2 className="size-3.5 animate-spin" /> : "Re-price"}
              </Button>
            </div>
          ) : (
            <>
              <table className="w-full text-[12px]">
                <tbody>
                  {quote.line_items.map((li) => (
                    <tr key={li.id} className="align-top">
                      <td className="py-1 pr-2">
                        <div className="font-medium">{li.description}</div>
                        <div className="text-muted-foreground text-[11px] tabular-nums">
                          {Number(li.quantity).toLocaleString()} {li.unit.replace("per_", "")} ·{" "}
                          <span className="font-mono">{li.catalog_code}</span>
                        </div>
                        {li.applied_rules.map((r) => (
                          <div key={r.code} className="text-muted-foreground text-[10px]">
                            {r.description} ({r.delta.display})
                          </div>
                        ))}
                      </td>
                      <td className="py-1 text-right font-medium whitespace-nowrap tabular-nums">
                        {li.subtotal.display}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <Separator className="my-2.5" />

              <dl className="space-y-1 text-[12px]">
                <Row label="Line subtotal" value={quote.line_subtotal.display} />
                {quote.adjustments.map((a) => (
                  <Row key={a.code} label={a.description} value={a.delta.display} muted />
                ))}
                <Row label="Subtotal" value={quote.adjusted_subtotal.display} />
                <Row label="Sales tax" value={quote.tax.display} muted />
                <Separator className="my-1.5" />
                <Row label="Total" value={quote.total.display} bold />
              </dl>

              <p className="text-muted-foreground mt-2 text-[10px]">
                Priced by engine v{quote.priced_by_engine_version}
              </p>
            </>
          )}
        </section>

        {/* 3. the drafted email */}
        <section className="bg-background p-5">
          <SectionLabel>What we&apos;d send</SectionLabel>
          <div className="text-muted-foreground mb-2 flex items-center gap-1.5 text-[11px]">
            <Mail className="size-3" />
            <span className="truncate font-mono">{quote.draft?.to_email ?? "no recipient"}</span>
          </div>
          <div className="mb-1.5 text-[12px] font-medium">{quote.draft?.subject}</div>
          {editing ? (
            <Textarea
              value={draftBody}
              onChange={(e) => setDraftBody(e.target.value)}
              className="min-h-52 text-[12px] leading-relaxed"
            />
          ) : (
            <pre className="bg-muted/40 max-h-64 overflow-auto rounded-md p-3 font-sans text-[12px] leading-relaxed whitespace-pre-wrap">
              {quote.draft?.edited_body ?? quote.draft?.body}
            </pre>
          )}
          {quote.draft?.was_edited && (
            <Badge variant="outline" className="mt-2 text-[10px] font-normal">
              edited by a human
            </Badge>
          )}
        </section>
      </div>

      {/* flags + decision bar */}
      <div className="border-t px-5 py-4">
        {jr.needs_review && (
          <div className="mb-3">
            <ReviewFlags
              needsReview={jr.needs_review}
              isNewCustomer={jr.is_new_customer}
              reasons={jr.review_reasons}
            />
          </div>
        )}

        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-56 flex-1">
            <Label htmlFor={`notes-${quote.id}`} className="text-[11px]">
              Decision notes
            </Label>
            <Input
              id={`notes-${quote.id}`}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Optional — recorded on the approval"
              className="mt-1 h-8 text-[12px]"
            />
          </div>

          {jr.latest_run_id && (
            <Link
              href={`/traces/${jr.latest_run_id}`}
              className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "h-8 text-[12px]")}
            >
              View trace
            </Link>
          )}

          <Button
            variant="outline"
            size="sm"
            className="h-8 gap-1.5 text-[12px]"
            onClick={() => act("reject")}
            disabled={disabled}
          >
            {busy === "reject" ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <X className="size-3.5" />
            )}
            Reject
          </Button>

          <Button
            size="sm"
            className="h-8 gap-1.5 text-[12px]"
            onClick={() => act("approve")}
            disabled={disabled}
          >
            {busy === "approve" ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Check className="size-3.5" />
            )}
            Approve &amp; send
          </Button>
        </div>
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-muted-foreground mb-2 text-[10px] font-semibold tracking-wider uppercase">
      {children}
    </div>
  );
}

function Row({
  label,
  value,
  muted,
  bold,
}: {
  label: string;
  value: string;
  muted?: boolean;
  bold?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className={cn("truncate", muted && "text-muted-foreground", bold && "font-semibold")}>
        {label}
      </dt>
      <dd
        className={cn(
          "whitespace-nowrap tabular-nums",
          muted && "text-muted-foreground",
          bold && "font-semibold"
        )}
      >
        {value}
      </dd>
    </div>
  );
}

function ParsedSummary({
  parsed,
  confidence,
}: {
  parsed: Record<string, unknown>;
  confidence: Record<string, unknown> | null | undefined;
}) {
  const size = parsed.property_size_sqft as number | null;
  const address = parsed.property_address as string | null;
  const urgency = parsed.urgency as string;
  const access = parsed.access_difficulty as string;
  const specials = (parsed.special_requests as string[]) ?? [];

  const conf = (key: string) => {
    const v = confidence?.[key];
    return typeof v === "number" ? v : null;
  };

  return (
    <dl className="mt-3 space-y-1 text-[11px]">
      <Field label="Address" value={address ?? "not stated"} score={conf("property_address")} />
      <Field
        label="Size"
        value={size ? `${size.toLocaleString()} sq ft` : "not stated"}
        score={conf("property_size_sqft")}
      />
      <Field label="Urgency" value={urgency} score={conf("urgency")} />
      <Field label="Access" value={access} score={conf("access_difficulty")} />
      {specials.length > 0 && (
        <div className="pt-1">
          <div className="text-muted-foreground">Special requests</div>
          <ul className="mt-0.5 space-y-0.5">
            {specials.map((s) => (
              <li key={s}>• {s}</li>
            ))}
          </ul>
        </div>
      )}
    </dl>
  );
}

function Field({ label, value, score }: { label: string; value: string; score: number | null }) {
  const low = score != null && score < 0.7;
  return (
    <div className="flex items-baseline justify-between gap-2">
      <dt className="text-muted-foreground shrink-0">{label}</dt>
      <dd className="flex min-w-0 items-baseline gap-1.5">
        <span className="truncate capitalize">{value}</span>
        {score != null && (
          <span
            className={cn(
              "shrink-0 tabular-nums",
              low ? "font-medium text-amber-600 dark:text-amber-400" : "text-muted-foreground/60"
            )}
          >
            {score.toFixed(2)}
          </span>
        )}
      </dd>
    </div>
  );
}

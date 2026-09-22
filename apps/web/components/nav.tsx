"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, BarChart3, Inbox, ShieldCheck } from "lucide-react";

import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/inbox", label: "Inbox", icon: Inbox },
  { href: "/approvals", label: "Approvals", icon: ShieldCheck },
  { href: "/traces", label: "Traces", icon: Activity },
  { href: "/evals", label: "Evals", icon: BarChart3 },
];

export function Nav({ pendingCount }: { pendingCount?: number }) {
  const pathname = usePathname();

  return (
    <header className="border-border/60 bg-background/80 sticky top-0 z-40 border-b backdrop-blur">
      <div className="mx-auto flex h-14 w-full max-w-[1600px] items-center gap-6 px-6">
        <Link href="/inbox" className="flex items-center gap-2.5">
          <div className="bg-foreground text-background grid size-6 place-items-center rounded-[5px] text-[11px] font-bold">
            GC
          </div>
          <div className="leading-none">
            <div className="text-[13px] font-semibold tracking-tight">GroundControl</div>
            <div className="text-muted-foreground mt-0.5 text-[10px]">Riverside Grounds</div>
          </div>
        </Link>

        <nav className="flex items-center gap-1">
          {LINKS.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || pathname.startsWith(`${href}/`);
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "relative flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[13px] transition-colors",
                  active
                    ? "bg-muted text-foreground font-medium"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                )}
              >
                <Icon className="size-3.5" />
                {label}
                {label === "Approvals" && pendingCount ? (
                  <span className="bg-foreground text-background ml-0.5 grid h-4 min-w-4 place-items-center rounded-full px-1 text-[10px] font-semibold tabular-nums">
                    {pendingCount}
                  </span>
                ) : null}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}

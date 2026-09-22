import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type Health = { status: string; service: string; model: string };

async function getHealth(): Promise<Health | null> {
  try {
    const res = await fetch(`${API_BASE}/health`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as Health;
  } catch {
    return null;
  }
}

export default async function Home() {
  const health = await getHealth();

  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center gap-6 p-8">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">GroundControl</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Operations copilot for Riverside Grounds
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between text-base">
            API connection
            {health ? (
              <Badge variant="default">connected</Badge>
            ) : (
              <Badge variant="destructive">unreachable</Badge>
            )}
          </CardTitle>
          <CardDescription className="font-mono text-xs">{API_BASE}</CardDescription>
        </CardHeader>
        <CardContent className="text-sm">
          {health ? (
            <dl className="grid grid-cols-[8rem_1fr] gap-y-1">
              <dt className="text-muted-foreground">service</dt>
              <dd className="font-mono">{health.service}</dd>
              <dt className="text-muted-foreground">pipeline model</dt>
              <dd className="font-mono">{health.model}</dd>
            </dl>
          ) : (
            <p className="text-muted-foreground">
              Start the stack with <code className="font-mono">docker compose up</code>.
            </p>
          )}
        </CardContent>
      </Card>

      <p className="text-muted-foreground text-xs">
        Phase 0 scaffold. The Inbox, Approval Queue, Trace Viewer, and Eval Dashboard arrive in
        Phase 5.
      </p>
    </main>
  );
}

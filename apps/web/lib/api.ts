/**
 * Typed API client.
 *
 * Every type here comes from `packages/api-types/schema.d.ts`, which is
 * generated from the FastAPI OpenAPI spec by `npm run gen:types`. Nothing in
 * the frontend hand-declares a server shape, so the contract cannot drift
 * without the build noticing.
 */
import type { components } from "@groundcontrol/api-types/schema";

type Schemas = components["schemas"];

export type Money = Schemas["Money"];
export type JobRequestSummary = Schemas["JobRequestSummary"];
export type JobRequestDetail = Schemas["JobRequestDetail"];
export type QuoteOut = Schemas["QuoteOut"];
export type LineItemOut = Schemas["LineItemOut"];
export type DraftMessageOut = Schemas["DraftMessageOut"];
export type ApprovalOut = Schemas["ApprovalOut"];
export type ApprovalQueueItem = Schemas["ApprovalQueueItem"];
export type CustomerOut = Schemas["CustomerOut"];
export type AgentRunSummary = Schemas["AgentRunSummary"];
export type AgentRunDetail = Schemas["AgentRunDetail"];
export type TraceStepOut = Schemas["TraceStepOut"];
export type EvalRunOut = Schemas["EvalRunOut"];
export type EvalRunDetail = Schemas["EvalRunDetail"];
export type EvalCaseResultOut = Schemas["EvalCaseResultOut"];
export type DashboardStats = Schemas["DashboardStats"];
export type CatalogOut = Schemas["CatalogOut"];
export type QuoteStatus = Schemas["QuoteStatus"];
export type JobRequestStatus = Schemas["JobRequestStatus"];
export type RunStatus = Schemas["RunStatus"];

/**
 * The API has two addresses and they are not the same.
 *
 * Server components render inside the `web` container, where the API is
 * reachable as `api:8000` on the compose network. The browser resolves nothing
 * on that network and must use the published `localhost:8000`. Using one value
 * for both means either the server pages render empty or the buttons fail.
 */
const BROWSER_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const SERVER_BASE = process.env.INTERNAL_API_BASE_URL ?? BROWSER_BASE;

export const API_BASE = typeof window === "undefined" ? SERVER_BASE : BROWSER_BASE;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string
  ) {
    super(`${status}: ${detail}`);
  }
}

type FetchOptions = {
  method?: string;
  body?: unknown;
  /** Server components read fresh data; there is no useful cache window here. */
  cache?: RequestCache;
};

async function request<T>(path: string, options: FetchOptions = {}): Promise<T> {
  const { method = "GET", body, cache = "no-store" } = options;

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    cache,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const parsed = await res.json();
      detail = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
    } catch {
      // Non-JSON error body; the status text is the best we have.
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Read helpers that return null instead of throwing when the API is down. */
async function safe<T>(path: string, fallback: T): Promise<T> {
  try {
    return await request<T>(path);
  } catch {
    return fallback;
  }
}

export const api = {
  health: () => safe<{ status: string; model: string } | null>("/health", null),

  jobRequests: (params?: { needs_review?: boolean; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.needs_review !== undefined) qs.set("needs_review", String(params.needs_review));
    if (params?.limit) qs.set("limit", String(params.limit));
    const suffix = qs.toString() ? `?${qs}` : "";
    return safe<JobRequestSummary[]>(`/api/job-requests${suffix}`, []);
  },

  jobRequest: (id: string) => request<JobRequestDetail>(`/api/job-requests/${id}`),

  approvalQueue: () => safe<ApprovalQueueItem[]>("/api/approvals", []),

  quote: (id: string) => request<QuoteOut>(`/api/quotes/${id}`),

  approve: (id: string, actor: string, notes?: string) =>
    request<Schemas["SendResultOut"]>(`/api/quotes/${id}/approve`, {
      method: "POST",
      body: { actor, notes: notes || null },
    }),

  reject: (id: string, actor: string, notes?: string) =>
    request<QuoteOut>(`/api/quotes/${id}/reject`, {
      method: "POST",
      body: { actor, notes: notes || null },
    }),

  edit: (
    id: string,
    payload: {
      line_items: { catalog_code: string; quantity: string; trunk_diameter_band?: string | null }[];
      actor: string;
      notes?: string | null;
      draft_subject?: string | null;
      draft_body?: string | null;
    }
  ) => request<QuoteOut>(`/api/quotes/${id}/edit`, { method: "POST", body: payload }),

  runs: (limit = 100) => safe<AgentRunSummary[]>(`/api/runs?limit=${limit}`, []),

  run: (id: string) => request<AgentRunDetail>(`/api/runs/${id}`),

  stats: () => safe<DashboardStats | null>("/api/stats", null),

  catalog: () => safe<CatalogOut | null>("/api/catalog", null),

  latestEval: () => safe<EvalRunDetail | null>("/api/evals/latest", null),

  evalRuns: () => safe<EvalRunOut[]>("/api/evals", []),
};

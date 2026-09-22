# GroundControl — Implementation Plan

> **Status:** Awaiting approval. No implementation code written yet.
> **Repo root:** `Forward Deployed Engineer (FDE)/groundcontrol/` (git initialized, branch `main`, zero commits).

---

## 0. Environment findings (these shaped the plan)

I checked the machine before planning. Four findings change concrete decisions:

| Finding | Impact | Decision |
|---|---|---|
| **`make` is not installed** (Windows, no GnuWin/choco make) | `make dev/seed/eval/test` can't run as-is | Ship a real `Makefile` (canonical; works in CI/Linux/WSL) **plus** `make.ps1`, a thin PowerShell shim with identical targets. README documents both: `make seed` or `.\make.ps1 seed`. The Makefile is not decorative — it is the source of truth; the shim dispatches the same underlying commands. |
| **Python 3.14.3 local; no 3.12; `uv 0.10.7` present** | Brief specifies Python 3.12 | Container is `python:3.12-slim` — authoritative. Local dev pinned via `.python-version` = `3.12` + `uv python install 3.12`; `uv sync` creates the venv. The Docker path needs no local Python at all. |
| **No `pnpm`; npm 11.12 / Node 24.15** | — | Use `npm` throughout. No workspace tooling beyond npm workspaces for `packages/`. |
| **Working dir path has spaces + parens** — `Forward Deployed Engineer (FDE)` | Compose derives its project name from the directory name; parens and spaces break bind mounts and some tooling | Repo root is a clean `groundcontrol/` subdirectory; `name: groundcontrol` pinned explicitly in `docker-compose.yml` so nothing is derived from the parent path. |

---

## 1. MVP scope restated (what "done" means)

One workflow, end to end, with rigor. Nothing else.

**The golden path:** raw job-request email → structured `JobRequest` → customer match (or *new* flag) → LLM-proposed line items → **deterministically priced** `Quote` → drafted customer email → **human approval queue** → human approves/edits/rejects → only then does a (mock) send fire and the quote move to `sent`.

**The three things that make it FDE-grade rather than a demo:**

1. **Eval harness** — real metrics on a labeled synthetic dataset, with thresholds, pass/fail, and JSON output.
2. **Observability** — every run traced (steps, tool I/O, tokens, latency, cost), viewable in the UI.
3. **`CASE_STUDY.md`** — the engagement writeup.

**Out of scope until the MVP is green and the eval passes:** crew scheduling, invoice anomaly detection, voice ingestion, real integrations, auth/multi-tenancy, payments. Adapter *interfaces* get stubbed where it keeps the seams clean (`CalendarAdapter`, `AccountingAdapter` defined with no-op mocks); no implementations.

---

## 2. Architecture

```
                         ┌─────────────────────────────────────────┐
  raw email (fixture) ──▶│  POST /api/job-requests                 │
                         └──────────────┬──────────────────────────┘
                                        │
                        ┌───────────────▼──────────────────────────────┐
                        │  Orchestrator  (services/api/agent/loop.py)  │
                        │  explicit, code-driven loop over a tool      │
                        │  registry; every call traced                 │
                        └───────────────┬──────────────────────────────┘
                                        │
      ┌──────────┬──────────┬───────────┼───────────┬──────────────┬─────────────┐
      ▼          ▼          ▼           ▼           ▼              ▼             ▼
  parse_job  lookup_    get_service  propose_   compute_quote  draft_      submit_for_
  _request   customer   _catalog     line_items  ◀ DETERMIN-   customer_   approval
   [LLM]     [code:     [code: DB]   [LLM]        ISTIC code   email       [code]
             rapidfuzz]                           no LLM]      [LLM]       └──▶ TERMINAL
                                                                                 │
                                        ┌────────────────────────────────────────▼──┐
                                        │  Approval queue  (status=pending_approval) │
                                        │  NOTHING leaves the system                 │
                                        └────────────────────┬───────────────────────┘
                                                human clicks │ approve / edit / reject
                                        ┌────────────────────▼───────────────────────┐
                                        │  send_quote(approval_id)                   │
                                        │  GUARD: refuses without approved Approval  │
                                        │  ──▶ MockMessagingAdapter ──▶ status=sent  │
                                        └────────────────────────────────────────────┘
```

**Key architectural commitments:**

- **FastAPI is the only backend.** Next.js is a pure client — no API routes, no server-side DB access, no business logic. The contract is the FastAPI OpenAPI spec.
- **The LLM proposes; code decides.** Three LLM touchpoints only (parse, propose line items, draft email). Money, DB writes, and sends are pure Python. `compute_quote` never sees the model.
- **`send_quote` is not in the agent's tool registry.** It is physically unreachable from the agent loop — only the approval API endpoint can call it, and it re-checks for an approved `Approval` row at the service layer. Guardrail by construction, not by prompt.
- **Explicit orchestration over model-driven looping.** The pipeline order is fixed and owned by code; the model is called for narrow, schema-constrained sub-tasks. Tradeoff documented in `CASE_STUDY.md` — we trade agent autonomy for determinism, traceability, and a bounded cost envelope, which is the right trade when the output is a price a business will honor.

### Tech decisions

| Layer | Choice | Note |
|---|---|---|
| API | Python 3.12 + FastAPI + Pydantic v2 | `uv` for deps, `pyproject.toml` |
| DB | Postgres 16 + SQLAlchemy 2.0 (typed, `Mapped[]`) + Alembic | Migrations owned by `services/api` |
| Money | `Decimal` in the engine, integer cents at rest | Never float |
| LLM | Official `anthropic` Python SDK | Structured outputs (`output_config.format`) + `strict: true` tools |
| Model | `LLM_MODEL` env var, default `claude-opus-5` | See §8 on cost |
| Fuzzy match | `rapidfuzz` | Deterministic, testable, no LLM |
| Tracing | In-house tracer → Postgres (`AgentRun`/`TraceStep`) | Rejected Langfuse: an extra service in compose, and traces become less legible to a reviewer who just wants to click a run. Documented as a deliberate call. |
| Web | Next.js 15 App Router + TS + Tailwind + shadcn/ui | npm |
| Types | `openapi-typescript` → `packages/api-types` | Regenerated by `make types`; drift is CI-checkable |
| Tests | `pytest` + `pytest-asyncio`; `FakeLLMClient` for determinism | Zero API spend in `make test` |
| Lint | `ruff` (API), `eslint` + `prettier` (web) | |

---

## 3. File / folder layout

```
groundcontrol/
├─ apps/web/
│  ├─ app/
│  │  ├─ layout.tsx, page.tsx            # → redirect to /inbox
│  │  ├─ inbox/page.tsx                  # Screen 1: Inbox / Runs
│  │  ├─ approvals/page.tsx              # Screen 2: Approval Queue (the money shot)
│  │  ├─ approvals/[quoteId]/page.tsx    #   detail + edit-before-send
│  │  ├─ traces/page.tsx                 # Screen 3: run list
│  │  ├─ traces/[runId]/page.tsx         #   expandable step waterfall
│  │  └─ evals/page.tsx                  # Screen 4: Eval Dashboard
│  ├─ components/  (ui/ = shadcn, plus domain components)
│  ├─ lib/api.ts                         # typed fetch client over generated types
│  └─ package.json, next.config.ts, tailwind.config.ts, Dockerfile
│
├─ services/api/
│  ├─ app/
│  │  ├─ main.py                         # FastAPI app, CORS, lifespan
│  │  ├─ config.py                       # pydantic-settings; all env
│  │  ├─ deps.py
│  │  └─ routers/  job_requests.py quotes.py approvals.py runs.py evals.py catalog.py customers.py
│  ├─ agent/
│  │  ├─ loop.py                         # the orchestrator
│  │  ├─ registry.py                     # tool registry + tracing decorator
│  │  ├─ tools/  parse.py match.py catalog.py propose.py price.py draft.py approve.py send.py
│  │  ├─ schemas.py                      # Pydantic I/O contracts for every tool
│  │  ├─ prompts/  *.md                  # versioned prompt files, not inline strings
│  │  ├─ llm.py                          # LLMClient protocol + AnthropicClient + FakeLLMClient
│  │  └─ guardrails.py                   # confidence thresholds, scope check, approval gate
│  ├─ pricing/
│  │  ├─ engine.py                       # pure functions, no I/O
│  │  ├─ rules.py                        # PricingRule application order
│  │  └─ catalog_data.py                 # seed catalog definitions
│  ├─ integrations/
│  │  ├─ base.py                         # MessagingAdapter / CRMAdapter / CalendarAdapter / AccountingAdapter Protocols
│  │  └─ mock/  messaging.py crm.py calendar.py accounting.py
│  ├─ observability/
│  │  ├─ tracer.py                       # run/step context managers
│  │  └─ cost.py                         # model → $/MTok table, usage → cost
│  ├─ evals/
│  │  ├─ run.py                          # `make eval` entrypoint
│  │  ├─ metrics/  extraction.py matching.py quote.py guardrails.py draft.py perf.py
│  │  ├─ thresholds.yaml
│  │  ├─ cache.py                        # on-disk LLM response cache (free re-runs)
│  │  └─ results/                        # JSON output, plus latest.json
│  ├─ db/
│  │  ├─ models.py, session.py
│  │  └─ migrations/                     # Alembic
│  ├─ fixtures/
│  │  ├─ job_requests.jsonl              # 150–200 labeled cases (versioned)
│  │  ├─ adversarial.jsonl               # ~15 cases
│  │  ├─ customers.json                  # ~40
│  │  └─ catalog.json                    # services + pricing rules
│  ├─ scripts/  seed.py generate_dataset.py
│  ├─ tests/  test_pricing.py test_guardrails.py test_matching.py test_pipeline.py test_api.py
│  └─ pyproject.toml, Dockerfile, alembic.ini
│
├─ packages/api-types/                   # generated TS types + openapi.json snapshot
├─ docker-compose.yml
├─ Makefile
├─ make.ps1                              # Windows shim, same targets
├─ .env.example
├─ .gitignore
├─ PLAN.md
├─ README.md
└─ CASE_STUDY.md
```

---

## 4. Data model

SQLAlchemy 2.0, UUID primary keys, `created_at`/`updated_at` everywhere. Money stored as `BigInteger` cents behind a `Money` value object.

| Entity | Key fields |
|---|---|
| **Customer** | name, primary contact (name/email/phone), addresses[], status `existing`/`new`, notes, `created_from_job_request_id` |
| **Address** | line1, city, state, postal, lat/lng (nullable), property_notes |
| **ServiceCatalogItem** | code, name, service_type, unit `per_sqft`/`per_hour`/`per_unit`/`flat`, base_rate_cents, min_charge_cents, description, active |
| **PricingRule** | code, kind `surcharge`/`multiplier`/`minimum`/`tax`, applies_to (service codes or global), condition (JSON), value, apply_order, active |
| **JobRequest** | raw_source_text, source `email`/`voicemail_transcript`, received_at, parsed (JSON), **per-field confidence (JSON)**, `needs_review` bool + `review_reasons[]`, customer_id (nullable), `is_new_customer`, status |
| **Quote** | job_request_id, quote_number, status `draft`→`pending_approval`→`approved`/`rejected`→`sent`, subtotal/adjustments/tax/total cents, currency, notes, `priced_by_engine_version` |
| **LineItem** | quote_id, catalog_item_id, description, quantity (Decimal), unit, unit_price_cents, subtotal_cents, applied_rule_codes[], `source` `llm_proposed`/`human_edited` |
| **DraftMessage** | quote_id, channel, subject, body, `generated_by_run_id`, `edited_body` (nullable) |
| **Approval** | quote_id, actor, action `approve`/`edit`/`reject`, decision_notes, edited_fields (JSON diff), decided_at, status `pending`/`resolved` |
| **AgentRun** | job_request_id, status, started_at/ended_at, total_tokens_in/out, total_cost_cents, model, error |
| **TraceStep** | run_id, seq, tool_name, input (JSON), output (JSON), tokens_in/out, latency_ms, cost_cents, status, error |
| **EvalRun** | started_at, model, n_cases, metrics (JSON), thresholds (JSON), passed |
| **EvalCase** / **EvalResult** | case_id, input, ground_truth, tags[] / per-metric scores, diffs |
| **SentMessage** | approval_id, to, subject, body, sent_at, adapter `mock` — what the MockMessagingAdapter writes |

**Enforced invariants (DB + service layer):**

- `Quote.status` transitions run through a state machine; illegal transitions raise.
- A `Quote` can only reach `sent` if an `Approval` with `action=approve, status=resolved` exists for it — checked inside `send_quote` *and* asserted by a unit test.
- `LineItem.subtotal_cents` is always recomputed by the engine, never accepted from input — including on human edit, where edits change quantity/service and the quote is then re-priced.

---

## 5. The agent

**Tool registry** — every tool is a Pydantic-in / Pydantic-out function wrapped by a `@traced` decorator that writes a `TraceStep`.

| Tool | Impl | Notes |
|---|---|---|
| `parse_job_request(email_text)` | **LLM**, structured output | Emits `JobRequestParsed` with per-field `confidence: float`. Contact fields are constrained: the prompt plus a post-check reject any email or phone number not literally present in the source text. |
| `lookup_customer(name,email,address)` | code, `rapidfuzz` | Weighted score across name/email/address; ≥0.88 → match, 0.65–0.88 → match + `needs_review`, <0.65 → `NewCustomerFlag`. Thresholds in config, tested. |
| `get_service_catalog()` | code, DB | Returns catalog plus active rules |
| `propose_line_items(job, catalog)` | **LLM**, strict schema | Returns `[{catalog_code, quantity, unit, rationale, confidence}]`. The schema has no price field — the model *cannot* express a price. An unknown `catalog_code` is a validation error that routes to a human. |
| `compute_quote(proposed)` | **code, deterministic** | The pricing engine. Validates minimum job size, non-negative totals, an absurd-total ceiling, and quantity sanity per unit type. |
| `draft_customer_email(quote)` | **LLM** | Given the *computed* totals. Post-check: every dollar figure in the body must appear in the quote, the quote number must be present, and totals must reconcile. Failure → one regeneration → route to human. |
| `submit_for_approval(quote, draft)` | code | Creates `Approval(status=pending)`, sets quote to `pending_approval`. **Terminal — the loop stops here.** |
| `route_to_human(reason)` | code | Alternate terminal for out-of-scope, incomplete, or contradictory input |
| `send_quote(approval_id)` | code, **not in registry** | Guard → `MockMessagingAdapter` → `SentMessage` row → quote `sent` |

**Guardrails, enforced in code:**

1. No customer-facing send and no committed total without a resolved approving `Approval` (the `send_quote` guard plus the state machine).
2. Any field below its confidence threshold, and any new customer, sets `needs_review` with a reason string — surfaced as a badge in the UI, never silently accepted.
3. A scope check runs before pricing: requests that are out of scope (services absent from the catalog), materially incomplete (no service or no location), or self-contradictory terminate at `route_to_human(reason)` with no quote generated.
4. `compute_quote` refuses to emit a quote with a total at or below zero, or above a configured sanity ceiling.

---

## 6. Synthetic data — generated label-first

The generation direction matters for label integrity, so: **write the ground truth first, then render the messy email from it.** A structured `GroundTruth` record (services, quantities, address, urgency, special requests, and an expected total computed by the *real* pricing engine) is authored programmatically with controlled variation. The LLM is then asked to write the email a human would have sent for that record, under a sampled "mess profile": typos, vague sizing ("the big field out back"), multiple services, a forwarded-thread wrapper, missing info, polite rambling, all-caps, phone-typed brevity.

Labels are therefore correct by construction rather than by annotation, and the expected total comes from the same engine the eval scores against — so quote correctness measures *the model's proposal quality*, not pricing drift.

- **~180 cases** across mess profiles, 1–3 services each.
- **~40 customers**, deliberately including near-duplicates (`Hillcrest Property Mgmt` / `Hillcrest Properties LLC`), a name-change case, and requests from genuinely new customers.
- **~15 adversarial cases**, hand-authored rather than generated: out of scope ("can you also do my taxes"), missing service, contradictory sizes ("quarter acre, about 500 sq ft"), instruction-injection text in the email body, and a request for a price the catalog cannot express.
- **Catalog:** ~14 services across mowing/maintenance, cleanup, tree work, hardscape, irrigation, and seasonal — mixed units. **Rules:** minimum job charge, tree-removal surcharge by trunk-diameter band, slope/access difficulty modifier, travel-zone surcharge, rush multiplier, seasonal multiplier, debris disposal per cubic yard, tax. Rich enough that pricing is genuinely non-trivial.
- Committed as versioned fixtures under `services/api/fixtures/`. Generation is a one-time script (`scripts/generate_dataset.py`), re-runnable but not required to run the project.

---

## 7. Eval harness

`make eval` → `python -m evals.run [--limit N] [--tags adversarial] [--no-cache]`

- **On-disk response cache** keyed by `(model, prompt_hash)`. The first full run costs real money; re-runs while iterating are free. `--no-cache` forces fresh calls. This is how spend stays controlled without gutting the dataset.
- `--limit N` for quick smoke runs.

| Metric | Definition |
|---|---|
| Extraction accuracy | Per-field exact/normalized match against ground truth across service types, size, address, special requests, urgency. Reported per-field **and** aggregate. |
| Customer-match accuracy | Confusion matrix: correctly linked / correctly flagged new / wrong link / missed link |
| Quote correctness | `abs(computed − expected) / expected ≤ 10%` counts as in-band; out-of-band cases are counted and listed with diffs |
| Guardrail pass rate | On adversarial cases, correctly routed to a human versus hallucinated a quote. **A hallucinated quote on an adversarial case is a hard fail, not a lost percentage point.** |
| Draft quality | Programmatic checks: no invented prices, quote number present, line items sum to the total, no placeholder text, length within band |
| Cost & latency | Mean tokens in/out, $/run, p50/p95 latency per stage |

Output: a formatted terminal table with each metric's threshold and PASS/FAIL, plus `evals/results/<timestamp>.json` and `latest.json`. Thresholds live in `thresholds.yaml`, checked into git so a reviewer can see the bar we set ourselves. The `EvalRun` row feeds the UI dashboard. **The README headline number is whatever this harness actually prints — no number gets written down that the harness didn't produce.**

---

## 8. Cost — flagging this for your decision

I'm defaulting to `LLM_MODEL=claude-opus-5` ($5/$25 per MTok). Rough per-quote envelope: ~5K input and ~1.7K output across three LLM calls, so **~$0.07–0.15 per quote** with adaptive thinking on.

That puts a **full 180-case eval run at roughly $15–30**, plus about $10 one-time for dataset generation. The response cache means you pay that once, not once per iteration.

`LLM_MODEL` is an env var, so `claude-sonnet-5` ($2/$10, ~$0.04/quote → ~$8 per full run) or `claude-haiku-4-5` ($1/$5) is a one-line change. **I'm not downgrading on my own initiative** — tell me if you want a different default. One thing worth doing either way: run the eval on two models and put the comparison in `CASE_STUDY.md`, which is exactly the kind of measured tradeoff an FDE reviewer wants to see. I'll confirm the cost with you again before the first full eval run.

I also need `ANTHROPIC_API_KEY` in the environment (or an `ant auth login` profile) before Phase 3. Everything through Phase 2 runs without it.

---

## 9. Phased task checklist

### Phase 0 — Scaffold and one-command run
- [ ] `.gitignore`, `.env.example`, README skeleton, license
- [ ] `services/api` pyproject (uv), FastAPI hello plus `/health`
- [ ] `apps/web` Next.js 15 + Tailwind + shadcn/ui init
- [ ] `docker-compose.yml` (postgres 16 + api + web, `name: groundcontrol`, healthchecks, hot reload)
- [ ] `Makefile` + `make.ps1` — `dev up down seed eval test types lint fmt migrate`
- [ ] **Gate: `docker-compose up` serves API and web from a clean clone**

### Phase 1 — Domain, DB, pricing engine
- [ ] SQLAlchemy models for every entity in §4; Alembic initial migration
- [ ] `Money`/cents value object; quote state machine
- [ ] Service catalog and pricing rule definitions
- [ ] **Pricing engine** — pure, ordered rule application, validation
- [ ] `test_pricing.py`: unit coverage including minimum charge, each rule, rule ordering, and edge cases (zero quantity, absurd total, unknown service, money rounding)
- [ ] **Gate: pricing tests green**

### Phase 2 — Synthetic data and seed
- [ ] `GroundTruth` schema plus controlled variation generator
- [ ] `scripts/generate_dataset.py` (label-first rendering); ~180 cases and ~40 customers
- [ ] Hand-author ~15 adversarial cases
- [ ] Commit fixtures; `scripts/seed.py` loads a coherent demo DB state
- [ ] **Gate: `make seed` produces a browsable demo state**

### Phase 3 — LLM layer, tracing, agent
- [ ] `LLMClient` protocol; `AnthropicClient` (structured outputs, `strict: true`, streaming, retries, typed error chain); `FakeLLMClient`
- [ ] Tracer plus cost table; `AgentRun`/`TraceStep` persistence
- [ ] Tools: parse → match → catalog → propose → compute → draft → submit/route
- [ ] `guardrails.py` and `test_guardrails.py` (no send without approval, adversarial routing, confidence flagging, invented-price rejection)
- [ ] Orchestrator loop
- [ ] `test_pipeline.py` — golden path against a fixed fixture with `FakeLLMClient`, fully deterministic and free
- [ ] **Gate: golden path runs end to end on seeded data; guardrail tests green**

### Phase 4 — API surface and type generation
- [ ] Routers for job requests, quotes, approvals (approve/edit/reject), runs/traces, evals, catalog, customers
- [ ] `send_quote` reachable only via the approval endpoint, with the guard re-checked
- [ ] `test_api.py`, including an explicit "attempt to send without approval returns 409" test
- [ ] `make types` → `openapi-typescript` → `packages/api-types`; drift-check target
- [ ] **Gate: the full loop is drivable over HTTP; types generate clean**

### Phase 5 — Frontend
- [ ] App shell, nav, typed API client, loading/empty/error states
- [ ] **Inbox / Runs** — request list, pipeline status, confidence and new-customer badges
- [ ] **Approval Queue** — raw email ↔ parsed fields ↔ itemized quote ↔ drafted email side by side; flags surfaced; Approve / Edit (re-prices live) / Reject with notes. This screen gets the most design effort.
- [ ] **Trace Viewer** — run list plus step waterfall, expandable I/O, tokens/latency/cost per step and per run
- [ ] **Eval Dashboard** — metrics table with threshold pass/fail, per-field extraction bar chart, cost and latency distribution
- [ ] **Gate: all four screens functional against the real API**

### Phase 6 — Eval harness
- [ ] Metric modules, `thresholds.yaml`, response cache, CLI flags
- [ ] Terminal table plus JSON output plus `EvalRun` persistence
- [ ] **Confirm cost with you, then run the full eval**
- [ ] Iterate prompts against results; record the improvement trajectory for the case study
- [ ] **Gate: eval green against thresholds; real numbers in hand**

### Phase 7 — Docs and polish
- [ ] `README.md` — what it is, the customer story, a mermaid architecture diagram, one-command run, **the headline eval number from the harness**, and an honest "what's mocked and how you'd make it real"
- [ ] `CASE_STUDY.md` — customer and problem, discovery, solution and tradeoffs, measured results, productionization path
- [ ] Phase 2+ interfaces stubbed and documented
- [ ] Clean-clone verification: `docker-compose up` + `make seed` + `make eval` + `make test`
- [ ] **Gate: every box in §13 of the brief ticked**

**Commits:** conventional, small, one logical change each — `feat(pricing): tree removal surcharge by trunk diameter`, `test(guardrails): reject send without approval`. The history should read like real engineering, not a single "initial commit" dump.

---

## 10. Decisions I need from you

Approve as-is and I'll proceed with the defaults below. Only these three are worth your input:

1. **Repo root at the `groundcontrol/` subdirectory** — because the parent path contains spaces and parens, which breaks Docker volume mounts. *(Default: yes; already initialized.)*
2. **`LLM_MODEL` default `claude-opus-5`**, at roughly $15–30 per full eval run, cached after the first. *(Default: Opus 5. Say the word for Sonnet 5 or Haiku 4.5.)*
3. **The `make` shim** — `Makefile` as the canonical target definitions plus `make.ps1` for this Windows box, rather than asking you to install GNU Make. *(Default: ship both.)*

Everything else I'll decide as a working engineer and document in the case study.

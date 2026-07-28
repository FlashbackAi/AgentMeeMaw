# Observability & Cost Dashboard — Design

**Date:** 2026-07-07
**Status:** Design (approved for spec review)
**Repo scope:** Python agent service. Node work is contract-only (a NODE_PROMPT doc). The dashboard UI is a separate repo, out of scope here.

---

## 1. Goal

A single internal observability dashboard for the whole Flashback app, backed by **live data on reload** (no streaming). It answers two questions:

1. **What are we spending?** — all API/LLM spend, sliced **by feature/pipeline** and **by model/provider**.
2. **What is the system producing?** — operational counts: legacies, storybooks, tributes, content volume, worker health.

Cost data does **not** exist anywhere today. There is no token counting, no usage metering, no metrics stack — only structlog JSON logs and direct Postgres queries. This design introduces the first cost ledger.

---

## 2. Ownership & boundaries

The relevant hard rules from `CLAUDE.md` §3:

- The agent (this repo) owns **all writes** to Postgres.
- **Node never writes Postgres directly.** When Node needs a write surface, the agent exposes an endpoint for it.
- The agent **never calls Node**; Node calls the agent.
- Node owns all **user-facing reads** from Postgres and is the **auth boundary**.

These rules fully determine the shape below:

- **Cost originates in two places.** Python sees OpenAI / Anthropic / Voyage token usage. Node sees image / video / voice generation usage (it consumes `artifact_generation`). Neither service alone sees total spend.
- **The agent is the sole writer** of the cost ledger. Python cost is inserted inline. Node's artifact-generation cost is sent to the agent via a new `POST /usage/events` endpoint, and the **agent** performs that insert.
- **Node is the read/serve tier.** It reads Postgres (the cost ledger + operational tables via read views) and serves JSON to the dashboard UI. "Data goes from Node to the client."
- **Everything is Postgres.** No DynamoDB. Volume is low enough that a single Postgres ledger is the right store.

### Data flow

```
  Python (this repo) ──INSERT──▶ ┌──────────────────────────────┐
  meters own LLM/embed cost      │ Postgres                     │
  (inline / background task)     │  • usage_events (NEW ledger) │
                                 │  • op tables (existing)      │
  Node ──POST /usage/events──▶   │  • dashboard_* views (NEW)   │
  agent validates + INSERTs      └──────────────────────────────┘
  the row (service='node')                 ▲
                                           │ reads only
                                     ┌─────┴──────┐
                                     │ Node       │ ──serves JSON──▶ Dashboard UI
                                     │ /dashboard │                  (separate repo)
                                     └────────────┘
```

**This repo builds:** the `usage_events` migration, Python cost metering, the `POST /usage/events` write endpoint, and the `dashboard_*` read views.
**Node builds (NODE_PROMPT only):** calling `POST /usage/events` after each generation, and the `/dashboard` read/serve API.
**Dashboard repo:** the UI, calling Node.

---

## 3. Data model — `usage_events`

A single append-only ledger. One row per metered unit of work.

```sql
CREATE TABLE usage_events (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  service            text        NOT NULL,            -- 'agent' | 'node'
  feature            text        NOT NULL,            -- pipeline that spent it (taxonomy §5)
  provider           text        NOT NULL,            -- 'openai'|'anthropic'|'voyage'|'gemini'|'elevenlabs'|...
  model              text        NOT NULL,
  input_tokens       int,
  output_tokens      int,
  cache_read_tokens  int,                             -- Anthropic prompt-cache reads (cheaper rate)
  cache_write_tokens int,                             -- Anthropic prompt-cache writes
  units              numeric,                         -- non-token work: image count, video seconds, audio chars
  unit_type          text        NOT NULL DEFAULT 'tokens',  -- 'tokens'|'images'|'video_seconds'|'audio_chars'
  cost_usd           numeric(12,6) NOT NULL,          -- each service computes its own $; ledger stores the number
  person_id          uuid,                            -- nullable; captured when in scope
  session_id         uuid,                            -- nullable
  created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX usage_events_created_at_idx ON usage_events (created_at DESC);
CREATE INDEX usage_events_feature_idx    ON usage_events (feature);
CREATE INDEX usage_events_model_idx      ON usage_events (provider, model);
```

### Design decisions

- **`cost_usd` is stored, not derived.** Each service computes cost from its own pricing map and writes the dollar figure. Node does not need Python's rate table and vice versa; the ledger just sums a number. Prices change, so persisting the computed cost also preserves historical accuracy.
- **`person_id` / `created_at` are captured even though v1 slices only by feature + model.** They are free to record and turn "cost per legacy" and time-series trends into pure queries later — no schema change, no re-instrumentation. The v1 dashboard simply won't surface them.
- **Append-only.** No updates, no supersession. It is a telemetry ledger, not canonical graph, so the graph invariants (`status`, edge repointing, etc.) do not apply.
- **Not embedded, not queued for embedding.** `usage_events` has no vector column and never touches the embedding queue.

---

## 4. Write paths (agent-owned)

### 4.1 Python-side metering (inline)

- **Single chokepoint.** Every text-LLM call funnels through `src/flashback/llm/interface.py` (`call_with_tool`, `call_text`, `call_text_stream`). Usage capture is added there: read `response.usage`, compute cost, record. For streaming, usage is captured from the final chunk (enable usage-in-stream on both providers).
- **Voyage embeddings** are not on that path — wrap the two embedding call sites (query embedder in the retrieval path; the embedding worker) separately.
- **Feature label.** Each caller passes a `feature` tag (taxonomy §5). Mechanical one-argument wiring at each call site.
- **Pricing** lives as a code constant map keyed by `(provider, model)` → `{input_per_mtok, output_per_mtok, cache_read_per_mtok, cache_write_per_mtok}`, plus a per-MTok rate for Voyage. Anthropic cache tokens are priced at their reduced rate (prompt caching is already enabled in `interface.py`). A `model_pricing` Postgres table is a later upgrade if no-deploy price edits are wanted; a constant is sufficient for v1.
- **Non-blocking on the hot path.** On the turn/response path the insert fires as a background task (`asyncio.create_task` against the existing async pool) so it never adds user-facing latency, and a failed insert is logged and swallowed — metering must never break a turn. In workers (extraction, trait synth, etc.) the insert is inline; latency there is irrelevant.
- **A thin `flashback.usage` module** owns cost computation and the write, so the capture logic lives in one place rather than smeared across call sites.

### 4.2 Node-side cost — `POST /usage/events`

A new agent endpoint (this repo). Node calls it after each image / video / voice generation; the agent inserts the row.

```
POST /usage/events
body: {
  feature: string,            -- e.g. 'artifact_image' | 'artifact_video' | 'voice'
  provider: string,           -- e.g. 'gemini' | 'elevenlabs'
  model: string,
  units?: number,             -- images / video_seconds / audio_chars
  unit_type?: string,         -- default 'tokens'
  input_tokens?: number,
  output_tokens?: number,
  cost_usd: number,           -- Node computes from its own pricing
  person_id?: uuid,
  session_id?: uuid
}
-> 201 { id }
```

- `service` is forced to `'node'` server-side (Node cannot spoof agent rows).
- No auth (Node is the auth boundary; trust is the service token + private network, per §8 invariant).
- The endpoint validates required fields and inserts. It does not compute cost — Node owns image/video/voice pricing.
- Batch variant (`POST /usage/events` accepting an array) is a reasonable extension if Node prefers to flush per-job groups; single-row is the v1 contract.

---

## 5. Feature taxonomy

The `feature` value names the pipeline that incurred the spend. Initial set:

**Agent, text LLM:**
`response_generate`, `intent_classify`, `segment_detect`, `rolling_summary`, `session_summary`, `tap_options`, `onboarding_parse`, `extraction`, `trait_merge`, `trait_synth`, `thread_naming`, `p4_questions`, `profile_summary`, `profile_facts`, `producer`, `identity_verify`.

**Agent, embeddings:**
`embedding_query` (retrieval), `embedding_row` (embedding worker).

**Node, generation (via `POST /usage/events`):**
`artifact_image`, `artifact_video`, `voice` (extend as Node's surfaces grow).

The set is open — an unknown `feature` is stored as-is (the ledger never rejects on taxonomy), so new pipelines don't require a schema or enum change.

---

## 6. Read surface — `dashboard_*` views

So Node's `/dashboard` API stays thin and does not couple to raw schema, this repo ships read-only views. Node reads these; the dashboard UI never touches Postgres directly.

- `dashboard_cost_by_feature` — `feature` → `sum(cost_usd)`, call count, token totals.
- `dashboard_cost_by_model` — `provider, model` → `sum(cost_usd)`, count, token totals.
- `dashboard_storybooks` — count by `status` × `collection`.
- `dashboard_tributes` — count by `status`, plus completion % off the existing `tribute_status` view.
- `dashboard_content_counts` — active `moments`/`entities`/`threads`/`traits`/`questions`; `persons` by `phase`.
- `dashboard_worker_health` — `extraction_outbox` backlog by `status`, attempts, recent `last_error`; `processed_*` throughput.

The underlying rows carry `created_at`, so Node can window ("today / this week / all time") in its own query without any view change. Cost views are intentionally **not** pre-windowed — they expose raw aggregates plus the timestamp so the serving layer decides the range.

---

## 7. Node handoff (contract only)

A `docs/OBSERVABILITY_DASHBOARD_NODE_PROMPT.md` will specify, for the Node repo:

- The `POST /usage/events` contract (§4.2) — call it after each image/video/voice generation, with Node-computed `cost_usd`.
- The `dashboard_*` views (§6) to read.
- A suggested `GET /dashboard` API returning `{ cost: { by_feature, by_model, total }, ops: { legacies, storybooks, tributes, content, workers } }`, windowed by a query param, for the UI repo.
- **Session counts** (started / completed) are Node's to add from its own session store — sessions live in Node/DynamoDB, not this Postgres, so they cannot come from the agent.

---

## 8. Out of scope for v1

Captured or noted, but not surfaced:

- **Per-legacy cost** and **time-series trend charts** — data is captured (`person_id`, `created_at`); not shown in v1.
- **Live SQS queue depth** — needs an AWS API call, not Postgres. `extraction_outbox` backlog is the Postgres-native proxy; true depth is a later Node/CloudWatch add.
- **Infra cost** (EC2 / RDS / S3 / SQS) — comes from AWS Cost Explorer, not our metering.
- **Provider-billing reconciliation UI** — we keep the existing Anthropic `metadata.user_id` / OpenAI `store` tags so provider dashboards remain ground truth to sanity-check computed totals, but reconciling them in-app is out of scope.

---

## 9. Testing

- **Cost computation** — unit tests over the pricing map: a known `usage` payload (incl. Anthropic cache tokens) produces the expected `cost_usd`; unknown model raises/flags rather than silently zero-costing.
- **Metering does not break the caller** — a failing `usage_events` insert on the hot path is swallowed and logged, and the turn still returns.
- **`POST /usage/events`** — validation (required fields), `service` forced to `'node'`, row lands in the ledger; malformed body 4xxs.
- **Views** — seed a handful of `usage_events` + op rows and assert the `dashboard_*` aggregates match.

---

## 10. Build summary (this repo)

1. Migration: `usage_events` table + indexes + `dashboard_*` views.
2. `flashback.usage` module: pricing map, cost computation, record helper (background task on hot path, inline in workers).
3. Instrument `llm/interface.py` (all three call paths, incl. streaming usage) and the two Voyage embedding sites, threading a `feature` label.
4. `POST /usage/events` route + validation.
5. `docs/OBSERVABILITY_DASHBOARD_NODE_PROMPT.md`.
6. Tests per §9.

# Node Integration — Observability & Cost Dashboard

This is the Node-side contract for the observability/cost dashboard. The **Python
agent service owns all writes** to the `usage_events` ledger and ships the read
views; **Node records its own generation cost through an agent endpoint, reads
the views, and serves the dashboard UI**. Everything lives in Postgres — no
DynamoDB for this feature.

Spec: `docs/superpowers/specs/2026-07-07-observability-dashboard-design.md`.

---

## 1. What the agent already does (no Node work needed)

The agent meters every Python-side LLM and embedding call and inserts a row into
`usage_events` with the computed `cost_usd`. That covers OpenAI (`gpt-5.1`),
Anthropic (Sonnet/Haiku), and Voyage embeddings. Node does **not** touch those.

## 2. What Node must do — record generation cost

Node consumes `artifact_generation` (image/video) and any voice generation, so
Node is the only place that sees that spend. Because **Node never writes Postgres
directly** (CLAUDE.md §3), send each cost event to the agent, which performs the
insert.

### `POST /usage/events`

Call once per generation, after you know the cost. No auth header (the agent is
unauthenticated behind Node + the service token + private network).

Request body:

| field | type | required | notes |
|---|---|---|---|
| `feature` | string | ✅ | pipeline tag, e.g. `artifact_image`, `artifact_video`, `voice` |
| `provider` | string | ✅ | e.g. `gemini`, `elevenlabs` |
| `model` | string | ✅ | model/endpoint id |
| `cost_usd` | number | ✅ | **Node computes this** from its own pricing |
| `unit_type` | string | — | `tokens` \| `images` \| `video_seconds` \| `audio_chars` (default `tokens`) |
| `units` | number | — | image count / seconds / chars |
| `input_tokens` | int | — | if the provider is token-billed |
| `output_tokens` | int | — | if the provider is token-billed |
| `cache_read_tokens` | int | — | optional |
| `cache_write_tokens` | int | — | optional |
| `person_id` | uuid string | — | attribution when known |
| `session_id` | uuid string | — | attribution when known |

`service` is **forced to `node`** server-side — you cannot post agent-attributed
rows. Response: `201 { "id": "<uuid>" }`. Missing a required field → `422`.
Database unavailable → `503`.

```bash
curl -X POST "$AGENT_BASE_URL/usage/events" \
  -H "content-type: application/json" \
  -d '{
    "feature": "artifact_image",
    "provider": "gemini",
    "model": "imagen-x",
    "unit_type": "images",
    "units": 1,
    "cost_usd": 0.04,
    "person_id": "…"
  }'
```

Metering must never break a generation: treat a non-2xx from `/usage/events` as a
soft failure (log + drop or retry on your own queue), never as a reason to fail
the artifact job.

## 3. What Node reads — `dashboard_*` views

Node reads these Postgres views (read-only) and serves them to the dashboard UI.
Cost views are **raw aggregates** plus the underlying `usage_events.created_at`,
so Node windows ("today / this week / all time") in its own query — the views
don't pre-window.

| view | columns | answers |
|---|---|---|
| `dashboard_cost_by_feature` | `feature, call_count, cost_usd, input_tokens, output_tokens` | what pipeline is expensive |
| `dashboard_cost_by_model` | `provider, model, call_count, cost_usd, input_tokens, output_tokens` | where the money goes |
| `dashboard_storybooks` | `status, collection, n` | storybooks created (by status/collection) |
| `dashboard_tributes` | `status, n` | tributes (by status; pair with the existing `tribute_status` view for completion %) |
| `dashboard_content_counts` | `active_moments, active_entities, active_threads, active_traits, active_questions, persons, persons_starter, persons_steady` | content volume + legacies |
| `dashboard_worker_health` | `status, n, max_attempts` | extraction backlog / retries |

For per-window cost, filter `usage_events` directly:

```sql
SELECT feature, sum(cost_usd) AS cost_usd, count(*) AS calls
FROM usage_events
WHERE created_at >= now() - interval '7 days'
GROUP BY feature ORDER BY cost_usd DESC;
```

## 4. What Node serves — suggested `/dashboard` API

Shape for the dashboard UI repo (window via query param, e.g. `?window=7d`):

```json
{
  "cost": {
    "by_feature": [{ "feature": "response_generate", "cost_usd": 12.34, "call_count": 900 }],
    "by_model":   [{ "provider": "anthropic", "model": "claude-sonnet-4-6", "cost_usd": 40.10 }],
    "total_usd": 82.55
  },
  "ops": {
    "legacies":   { "total": 210, "starter": 40, "steady": 170 },
    "storybooks": [{ "status": "complete", "collection": "childhood", "n": 12 }],
    "tributes":   [{ "status": "complete", "n": 8 }],
    "content":    { "moments": 5400, "entities": 2100, "threads": 320 },
    "workers":    [{ "status": "pending", "n": 3, "max_attempts": 2 }]
  }
}
```

## 5. Sessions — Node's own data

Session counts (started / completed) are **not** in the agent Postgres — sessions
live in Node/DynamoDB. Add those to the dashboard from your own session store.

## 6. Feature taxonomy (agent side, for alignment)

So `cost_by_feature` labels line up, these are the `feature` values the agent
writes. Use matching values for your own generation events.

**Agent — text LLM:** `response_generate`, `intent_classify`, `segment_detect`,
`tap_options`, `ground_truth_tap`, `onboarding_parse`, `identity_verify`,
`profile_facts`, `node_edit`, `theme_archetype`, `extraction`,
`extraction_compat`, `entity_merge`, `trait_merge`, `trait_synth`,
`thread_naming`, `p4_questions`, `profile_summary`, `producer`,
`session_summary`, `tribute_assembly`, `tribute_message`, `tribute_video`,
`storybook_tagging`, `storybook_script`.

**Agent — embeddings:** `embedding_query`, `embedding_row`.

**Agent — image generation:** `tribute_image`, `storybook_image` — the
agent's own Gemini illustration calls inside the tribute/storybook render
workers (`unit_type='images'`, `units` = image count, flat per-image
pricing). These are agent spend, not yours — do **not** reuse these labels
for Node-emitted events.

**Node — generation (you emit these):** `artifact_image`, `artifact_video`,
`voice` (extend as your surfaces grow).

> Note: `segment_detect` covers a single LLM call that also regenerates the
> rolling summary — those two costs are not separable and are labeled
> `segment_detect`.

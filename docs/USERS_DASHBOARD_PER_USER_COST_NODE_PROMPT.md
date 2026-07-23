# Node Integration — Per-User Cost Attribution (Phase 21 users dashboard)

This closes the one open item from the Phase 21 coordination: the **per-user
cost panel** in the users-dashboard drill-down (`GET /dashboard/users/:userId`).

**Nothing here is an API change.** The agent exposes no new endpoint and the
`usage_events` read surface is unchanged. The only change is that agent-written
`usage_events` rows now **carry `person_id`**, so your existing per-user cost
query stops undercounting. If you did nothing, the panel would keep working —
it would just fill in on its own as attributed rows accrue.

Prior state and the decision to attribute: the observability dashboard
contract, `docs/OBSERVABILITY_DASHBOARD_NODE_PROMPT.md` §1.

---

## 1. What the agent changed (no Node code needed)

Before: every agent-written `usage_events` row (LLM, embedding, image render)
had `person_id = NULL`. Your `WHERE person_id = ANY($1)` per-user query saw only
Node's own generation spend (voice + image/video), so per-user cost undercounted
— typically a small fraction of true cost, since the bulk of spend is the
agent's Sonnet/`gpt-5.1` calls.

Now: the agent binds `(person_id, session_id)` once at each turn/job boundary
and stamps it onto the usage rows those calls produce. Covered features:

| Source | Features now attributed |
|---|---|
| Turn loop | `response_generate`, `intent_classify`, `segment_detect`, `tap_options`, `ground_truth_tap`, `session_summary`, per-turn `embedding_query` |
| Extraction | `extraction`, `entity_merge`, `trait_merge`, `extraction_compat`, `embedding_query` |
| Background workers | `producer`, `thread_naming`, `p4_questions`, `trait_synth`, `profile_summary`, `profile_facts` |
| Render workers | `tribute_assembly`, `tribute_message`, `tribute_video`, `tribute_image`, `storybook_tagging`, `storybook_script`, `storybook_image` |
| Identity-merge scan | `identity_verify` |

This is essentially all agent spend (the two dominant line items —
per-turn `response_generate` and per-segment `extraction`, both Anthropic
Sonnet — are both covered).

Also shipping on the agent side: **migration `0049`** adds a partial index
`usage_events (person_id) WHERE person_id IS NOT NULL`, so your
`= ANY($1)` drill-down is an index scan, not a seq scan. It deploys with the
agent; **no Node action and no new grant** (you already read `usage_events`).

---

## 2. Two things stay NULL **by design** — do not treat as a bug

1. **`embedding_row`** (the async embedding worker). It issues one Voyage batch
   call spanning *multiple* persons, so a single row can't honestly be
   attributed to one user. Attributing it would require a payload schema change
   plus per-message metering with proportional token-splitting — deferred,
   because Voyage embeddings are the cheapest line item. These rows stay
   `person_id = NULL`.
2. **Three low-volume `gpt-5.1` route calls** — `onboarding_parse` (~twice per
   legacy at onboarding), `theme_archetype` (cached, once per theme unlock),
   `node_edit` (per artifact edit). Not yet bound; may show NULL. Pennies.

Everything else attributes. If you want a hard "zero NULL agent rows" guarantee
for the panel, ping us and we'll bind the route tail too — but the cost there is
negligible.

---

## 3. Attribution is forward-looking — historical rows stay NULL

We do **not** backfill. `usage_events` rows written *before* this deploy have no
recoverable person association and remain `person_id = NULL` forever. So:

- Per-user cost is complete only for spend **after the agent deploy date**.
- The all-time per-user total will read low until enough post-deploy traffic
  accrues; a **windowed** per-user cost (e.g. "last 7 days") becomes accurate
  once the window sits fully after the deploy.
- The **global** `/dashboard` cost total (Phase 17) is unaffected either way —
  it sums all rows regardless of attribution.

Consider defaulting the per-user cost panel to a post-deploy window, or noting
the deploy date as the attribution start, so an early all-time total isn't read
as complete.

---

## 4. Verify it's live (run after the agent deploys + a little traffic)

```sql
SELECT feature,
       count(*) FILTER (WHERE person_id IS NOT NULL) AS attributed,
       count(*)                                      AS total
FROM usage_events
WHERE service = 'agent'
  AND created_at >= '<agent deploy timestamp>'
GROUP BY feature
ORDER BY total DESC;
```

Expect `attributed ≈ total` for `response_generate`, `extraction`,
`embedding_query`, `producer`, `profile_summary`, `trait_synth`,
`thread_naming`, the `tribute_*` / `storybook_*` render features, and
`identity_verify`. Expect `embedding_row` (and the three route features above)
to stay at `0` — that's correct.

Per-user drill-down (unchanged from what you already run):

```sql
SELECT feature, sum(cost_usd) AS cost_usd, count(*) AS calls
FROM usage_events
WHERE person_id = ANY($1)          -- this user's person_ids
GROUP BY feature
ORDER BY cost_usd DESC;
```

---

## 5. Node action items

1. **Remove the interim caveat** in the users-dashboard FE contract
   (`legacy/FRONTEND_USERS_DASHBOARD_PROMPT.md`, the dated §5 "per-user cost is
   generation-only" note) **once you've confirmed** the spot-check in §4 shows
   the LLM/embedding features attributing. Keep it until then — pre-deploy the
   panel is still generation-only.
2. **Grant check** (the Phase 21 open item): run the `node_readonly`
   `role_table_grants` check from prod and send us any missing tables across the
   Phase-21 read surface (`active_moments/entities/threads/traits/questions`,
   `tributes`, `active_storybooks`, `usage_events`). We'll add a guarded grant
   migration for any gap.
3. **Nothing else.** No endpoint changes, no query changes, no new grant for the
   index.

---

## 6. Confirmed earlier — no action

- `usage_events.person_id` column exists and is persisted. ✅
- `tributes.person_id` / `active_storybooks.person_id` present, `NOT NULL`,
  indexed. ✅
- The `dashboard_*` and `active_*` views are the intended read surface. ✅

---

## Handshake

We'll ping you when the agent deploy carrying this (code + migration `0049`) is
live. That's your trigger to run §4, drop the FE §5 caveat, and confirm the
per-user cost panel reconciles from generation-only to complete.

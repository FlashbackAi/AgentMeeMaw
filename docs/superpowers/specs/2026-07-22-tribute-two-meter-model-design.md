# Two-meter tribute model — standalone Tribute + occasion Campaign

**Date:** 2026-07-22
**Status:** approved design, ready for implementation
**Supersedes the meter contract in:** migration 0030 (`tribute_status`),
`checklist.py`, the `/generate` percent gate.

---

## Problem

Today there is one meter per `tributes` row scored memories 40 / message 30 /
appearance 20 / signature 10 (migration 0030). Three of the four slots
(memories, appearance, signature) derive from the shared person graph, so a
"campaign" tribute and a "standalone" tribute are not two clean meters — they're
separate rows whose graph-derived slots move together, differing only in the
per-row `message`. The meter also jumps in coarse blocks ("four quarters") and
every relationship is forced through the same message-centric flow.

We want two distinct products:

- **Standalone Tribute** — an always-on keepsake. Auto-starts for every legacy,
  accrues as the user simply talks to the AI, and unlocks a **simplified** video
  (no "one thing to say" message). Smooth, percentage-based meter.
- **Occasion Campaign** (Father's Day, Friendship Day, …) — opt-in. The user
  starts it, answers the archetype prompts, keeps talking, and once the stories
  are there the AI asks the campaign's "if you could say one thing…" message as
  the climax, then the video generates. Its own separate progress bar.

## Decisions (locked with the user)

1. **Shared base + extras on top.** There is one person graph. Both meters read
   the same graph facts (memories/appearance/signature). Standalone = base
   **minus** message. Campaign = base **plus** archetype + message. No
   per-campaign moment attribution (that was the rejected "fully independent"
   option).
2. **Standalone meter is smooth, memories-led.** Memories is the big continuous
   driver, graded by depth (sensory + time-anchor) toward a target of ~5 rich
   stories; appearance + signature are smaller. The bar moves on good turns
   rather than snapping full at a threshold.
3. **Campaign meter keeps today's 4 weighted slots** (memories 40 / message 30 /
   appearance 20 / signature 10). Archetype answers feed the memories
   answer-floor (0030). The message can be given any time, but the AI invites it
   as the climax once stories are present.
4. **Appearance + signature are soft by default, and CRM-configurable.** The
   hard gate (what makes a video *unlockable*) is memories (+ message on
   campaigns). Appearance + signature add to the % and the AI leads toward them
   (ground-truth taps, invariant #26) but don't block generation. A campaign can
   flip `require_appearance` / `require_signature` on to move them into its hard
   gate.
5. **Unlock ≠ 100%.** Because soft slots don't gate, a video *unlocks* at the
   hard gate and the bar continues to 100% as soft slots fill. `/generate` gates
   on `ready` (hard gate met), not `percent == 100`.
6. **Auto-start via a row at person creation.** A standalone row
   (`campaign_id = null`) is created in `insert_person`, alongside the seeded
   tribute theme. Existing legacies get a one-time backfill. Everything reads the
   same `tribute_status` view.

## Data model

`tributes` (unchanged columns; `campaign_id` already nullable, migration 0040):

- **Standalone row:** `campaign_id IS NULL`. Exactly one active per person
  (partial unique index — see migration). `message_text` stays null forever
  (no message path).
- **Campaign row:** `campaign_id = <campaign>`. One open row per campaign, as
  today (migration 0040 lifecycle).

New config (CRM, per campaign): `tribute_campaigns.require_appearance BOOLEAN
DEFAULT false`, `require_signature BOOLEAN DEFAULT false`. Standalone has no
campaign, so it uses the code defaults (both soft).

## The `tribute_status` view (rewrite)

Branch on `campaign_id IS NULL`. Both branches expose the same columns; the
weighting and gate differ.

Shared graph sub-selects (unchanged from 0030): `qualifying_count`,
`moment_score` (depth-weighted), `appearance_present`, `signature_present`,
`answered_layers` (campaign only — archetype answers on the linked theme).

**Standalone branch** (`campaign_id IS NULL`) — memories-led, no message:

```
memories_pct   = LEAST(moment_score, MEM_TARGET) / MEM_TARGET * 70      -- MEM_TARGET = 5.0 depth-weighted
appearance_pct = appearance_present ? 20 : 0
signature_pct  = signature_present ? 10 : 0
percent        = round(memories_pct + appearance_pct + signature_pct)
ready          = qualifying_count >= 3        -- the proven story floor; soft slots never gate standalone
```

**Campaign branch** (`campaign_id` set) — today's 4 slots + configurable soft gate:

```
memories_pts = GREATEST( answer_floor(answered_layers),                 -- 0030, caps 16/40
                         LEAST(moment_score, 3.0)/3 * 40 )
message_pts  = message_present ? 30 : 0
appr_pts     = appearance_present ? 20 : 0
sig_pts      = signature_present ? 10 : 0
percent      = round(memories_pts + message_pts + appr_pts + sig_pts)
ready        = qualifying_count >= 3
             AND message_present
             AND (NOT require_appearance OR appearance_present)
             AND (NOT require_signature  OR signature_present)
```

`percent` is a smooth 0–100 in both branches; `ready` is the unlock boolean.
The view joins `tribute_campaigns` to read `require_appearance/require_signature`
for campaign rows (defaults false when null / standalone).

`MEM_TARGET`, the story floor (3), and the depth weights are tunable in the view;
`checklist.py` mirrors them as documentation only (SQL stays the source of
truth, per the existing invariant).

## `/generate` gate change

Today: `if progress.percent < 100: 409`. New: `if not progress.ready: 409` with a
message naming what's missing (stories, or message on a campaign). Everything
downstream (context compose, snapshot, enqueue) is unchanged.

## Flow & steering

- **Standalone**: no opt-in, no message step, accrues from ordinary
  conversation. `select_message_invitation` and `POST /tributes/{id}/message`
  are **campaign-only** — they no-op / 409 for a standalone (`campaign_id IS
  NULL`) row.
- **Campaign**: opt-in (unlock → archetype) creates the campaign row. Once
  `qualifying_count >= 3`, steering surfaces the message invitation ("if you
  could say one thing…"); the message fills the 30-pt slot and completes the
  hard gate → `ready` → generate.

## Surfaces / build footprint (agent)

1. **Migration 0048**: rewrite `tribute_status` (two branches, smooth memories,
   `ready`≠percent, join campaign require-flags); add
   `require_appearance`/`require_signature` to `tribute_campaigns`; partial
   unique index for one active standalone row per person.
2. **`insert_person`**: create the standalone tribute row in the same tx as the
   theme seeding. **Backfill script** for existing legacies
   (`scripts/backfill_standalone_tributes.py`).
3. **config_schema / config_repository / config_llm / admin validation / CRM**:
   the two require-flags on the campaign (mirrors the render_engine / narrative
   pattern).
4. **`/generate`**: gate on `ready`.
5. **Message step**: guard `select_message_invitation` + `/tributes/{id}/message`
   to campaign rows only.
6. **checklist.py / progress.py**: two skins — standalone (3 slots: stories,
   appearance, signature) and campaign (4 slots incl. message). `progress`
   payload carries `kind` (`standalone` | `campaign`), `ready`, `percent`,
   per-slot sub-progress, and (campaign) `answered_layers`.
7. **Tests**: view math both branches, ready≠percent, require-flag gating,
   standalone row creation + backfill, `/generate` ready-gate, message-step
   guard.

## Node / frontend

Node consumes `tribute_status` (per row) and the `/tributes/*` endpoints. Both
rows surface as separate items (standalone `campaignId: null`; campaign rows
carry campaign identity). A Node prompt
(`docs/TRIBUTE_TWO_METER_NODE_PROMPT.md`) specifies exactly what Node must build
and mandates that Node's own frontend prompt answer the open UX questions and
set the flow (two bars, unlock-before-100%, standalone has no message UI,
campaign message-as-climax, which bar the in-session gutter shows).

## Out of scope

- Per-campaign moment attribution (rejected in favor of shared base).
- Changing the render pipeline, layouts, recipe, or narrative framing (shipped).
- Any auto-generation — unlock enables the user to generate; it never
  auto-fires.

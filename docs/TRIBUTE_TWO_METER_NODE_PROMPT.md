# Node Prompt — Two-meter tribute model (standalone Tribute + occasion Campaign)

**For:** the Node Backend team. You own the API surface the frontend calls and
the read of the `tribute_status` view. This prompt gives you everything to
build the Node side AND to author the frontend prompt.
**Agent status:** merged — migration 0048, the rewritten `tribute_status` view,
standalone auto-start, `/generate` ready-gate, campaign-only message step, and
the `require_appearance`/`require_signature` campaign config. Design:
`docs/superpowers/specs/2026-07-22-tribute-two-meter-model-design.md`.

> **You must produce a frontend prompt from this.** §7 lists the exact
> questions your frontend prompt has to answer and the flow it must encode. Do
> not hand the frontend a vague "show the meter" — hand it §7 fully resolved.

---

## 1. The model in one paragraph

Every legacy now has TWO kinds of tribute, both read from `tribute_status`,
distinguished by `meter_kind`:

- **`standalone`** (`campaign_id` = null) — the always-on keepsake. Auto-created
  when the legacy is created; **no opt-in, no "one thing to say" message**. Its
  meter climbs smoothly as the user simply talks to the AI, and the video
  **unlocks** once there are enough stories. Simplified.
- **`campaign`** (`campaign_id` set) — an occasion (Father's Day, Friendship
  Day). **Opt-in**: the user starts it, answers the archetype prompts, keeps
  talking, and once the stories are there the AI asks the campaign's
  "if you could say one thing…" message as the climax — then it unlocks.

Both draw from the same person graph, so talking fills the shared story/
appearance/signature progress for both; the campaign adds archetype + message
on top. A legacy can show **both meters at once** (the standalone always, plus
any campaign the user opted into).

## 2. `tribute_status` — the read surface (per row)

New/changed columns you must consume:

| column | meaning |
|---|---|
| `meter_kind` | **NEW.** `'standalone'` or `'campaign'`. Drives which UI to render. |
| `percent` | 0–100, **smooth**. Standalone is memories-led (no message term); campaign is the 4 weighted slots. |
| `ready` | **NEW meaning.** The unlock/generate gate — **decoupled from percent**. A video can be `ready` well below 100%. Gate the Generate button on THIS, not `percent === 100`. |
| `campaign_id` / `campaign_slug` / `campaign_display_name` | null on the standalone row; set on campaign rows. |
| `memories_count`, `message_present`, `appearance_present`, `signature_present`, `answered_layers` | per-slot signals, unchanged. |

There is exactly **one standalone row per legacy** (auto-created) and **zero or
more campaign rows**. Return all non-superseded rows for a legacy; the frontend
renders the standalone bar always and a campaign bar per opted-in campaign.

## 3. Unlock ≠ 100% (the thing most likely to trip you up)

`percent` is a smooth progress bar. `ready` is a separate boolean that flips
when the **hard gate** is met:

- **standalone**: enough stories (the story floor — 12 qualifying moments
  since migration 0051). Signature is *soft* — it raises the bar toward 100%
  and the AI pursues it, but it never blocks. So a standalone can be
  `ready: true` at 85%.
- **campaign**: enough stories **+ the message present** — plus appearance
  and/or signature *only if that campaign has `require_appearance` /
  `require_signature` on* (CRM toggles, default off).

So: **show Generate when `ready` is true**, even if the bar isn't full. The bar
keeps filling afterward as soft slots land; that's expected, not a bug.

## 4. Endpoints (behavior changes)

- **`POST /tributes/{id}/generate`** — now **409s unless `ready`** (was: unless
  `percent === 100`). The 409 body names what's missing ("enough shared
  stories", or "enough stories and your message"). Same request/response shape
  otherwise.
- **`POST /tributes/{id}/message`** — **409 on a standalone row**
  (`campaign_id` null): the standalone has no message step. Only POST it for
  campaign rows. (The in-chat message tap also never fires for standalone.)
- **Session start / unlock** — unchanged: campaigns are entered via the
  existing unlock → archetype → `/session/start` path. The standalone needs
  **no entry** — it already exists and accrues from ordinary `/turn`s.
- **`GET /tributes/{id}/progress`** (agent-internal live meter) now returns a
  `kind` field alongside `percent`/`ready`/`slots`; standalone omits the
  `message` slot.

## 5. The `require_appearance` / `require_signature` campaign toggles

Two new booleans on a campaign (CRM, default false). When on, that soft slot
joins the campaign's `ready` gate. Pure config — you already proxy the campaign
CRUD; just let the two keys through. No frontend work beyond what the meter
already shows.

## 6. What is UNCHANGED

Render pipeline, layouts, recipe, narrative framing, presigned URLs,
`tribute_render_complete` NOTIFY, URL columns — all as before. This change is
purely the meter/row model and the two gates above.

---

## 7. REQUIRED — your frontend prompt MUST answer these and set this flow

When you write the frontend prompt, resolve every item below explicitly. Do not
leave them to the frontend's discretion.

### 7a. Questions the frontend prompt must answer

1. **Two bars.** The legacy screen shows the **standalone Tribute meter always**
   (from the `meter_kind='standalone'` row), and **one campaign meter per
   opted-in campaign** (each `meter_kind='campaign'` row). State how they're laid
   out (e.g. standalone as the primary keepsake bar; campaign(s) as a separate
   card/section while active).
2. **Unlock uses `ready`, not 100%.** The Generate/"Create video" affordance
   appears when `ready === true`, even if `percent < 100`. The bar continues to
   fill afterward. Say this explicitly so the frontend doesn't gate on percent.
3. **Standalone has no message UI.** Never render a "say one thing to them"
   input on the standalone meter, and never POST `/tributes/{id}/message` for a
   `campaign_id: null` row (it 409s). The message belongs to campaigns only.
4. **Campaign message is the climax.** For a campaign, the "if you could say one
   thing…" prompt appears once stories are in (the agent surfaces it in-chat;
   the campaign card also shows it out-of-chat once `memories_count` ≥ floor and
   `message_present` is false). Answering it completes the campaign gate.
5. **In-session gutter.** While the user is in a session, the meter shown in the
   turn gutter is: the **campaign meter if the session was started for a
   campaign** (`session_metadata.theme_id` + campaign), otherwise the
   **standalone meter**. Specify this binding.
6. **New user during an active campaign.** A brand-new legacy shows the
   standalone meter building from 0% immediately; the active campaign is an
   **optional overlay** they can opt into — it does not replace or auto-start.
7. **Percent is smooth.** Render the bar as a continuous percentage, not four
   quarter-blocks. The per-slot breakdown (from `slots`) is secondary detail.

### 7b. The flow the frontend prompt must encode

```
Legacy created
   └─ standalone Tribute row auto-exists → meter at 0%, building as the user talks
        └─ ready (enough stories) → "Create your Tribute video" (no message step)

User opts into a live campaign (e.g. Friendship Day)
   └─ unlock → answer archetype prompts → campaign row created
        └─ keep talking → stories accrue (shared graph)
             └─ AI asks the campaign's "one thing to say" message (climax)
                  └─ ready (stories + message [+ required soft slots]) → "Create your Friendship Day video"

Both meters persist independently; each generates its own video on its own ready.
```

### 7c. Copy distinction

The end-user artifact is a **"Flashback"** either way. The standalone is the
plain keepsake Flashback; a campaign one is the occasion Flashback (skinned by
the campaign). "Standalone vs campaign" is an internal distinction — don't
surface those words to end users; surface the occasion name for campaigns and a
neutral "Your Tribute"/"A Tribute" for the standalone.

---

## 8. Rollout

- Agent deploy runs migration 0048 automatically and starts auto-creating the
  standalone row for **new** legacies. Run
  `scripts/backfill_standalone_tributes.py` once so **existing** legacies get
  their standalone row (idempotent, safe to re-run).
- No render-host or queue changes.

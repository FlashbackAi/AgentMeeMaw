# New campaign on an existing legacy — the full application flow

The case: a father legacy already has its Father's Day tribute video,
complete. A new campaign runs (with its own questions). What the
application must do, and what the agent now guarantees.

**Agent-side is DONE** (migrations 0041 + 0042 + fixes; one redeploy +
`migrate up`). This doc is the consumer-application contract.

## What the agent now guarantees

1. **The old video is never touched.** A new campaign entry gets its own
   tribute row; the completed one keeps its video/PDF forever
   (gallery doc: `TRIBUTE_VIDEO_GALLERY.md`).
2. **The new campaign's questions actually arrive.** `unlock_prepare`
   resolves campaign bank → profile bank → cache → AI. (Before: a legacy
   that ran the FD flow had 4 cached questions permanently shadowing
   every future bank — live in prod on the father legacy.)
3. **Answers are per-campaign.** Each tribute row accumulates the
   answers given under it (merged by question text on re-entry). The FD
   answers stay with the FD tribute; the meter for a new campaign counts
   its own answers (falling back to the legacy theme-row answers only
   for old tributes).
4. **Targeting** (0041): none of this happens at all for a legacy whose
   relationship the campaign doesn't cover.

## The application flow, step by step

User opens a legacy while campaign X is active (check
`GET /persons/:id/tribute-campaigns` → `active_featured_slug`, now
person-scoped when Node forwards `person_id`):

1. **Campaign card tap** → `POST /themes/{tribute_theme_id}/unlock_prepare`
   with `{person_id, campaign: "<slug>"}`. Response now carries:
   - `archetype_questions` — campaign X's questions (fresh, even if the
     theme is already unlocked from a past campaign);
   - `tribute_answered` — answers already committed on THIS campaign's
     open tribute (empty array/null on first entry);
   - `archetype_answers_draft` — mid-flow draft (existing behavior).
2. **Show the question modal** when any returned question has no entry
   in `tribute_answered` (match by `question_text`). Prefill the ones
   that match. If everything is covered, skip straight to step 3.
   *Do not skip the modal just because the theme is `unlocked` — that
   was the old FD-only assumption.*
3. **`POST /session/start`** with `session_metadata.theme_id`,
   `campaign: "<slug>"`, `archetype_answers: [...]`. The agent opens or
   reuses campaign X's tribute row (never another campaign's, never a
   completed one), stamps it, and merges the answers onto it.
4. **Progress meter** (`GET /tributes/{id}/progress`) — the answers
   component is per-campaign now. A rich legacy usually rides the
   moments component anyway, so a mature legacy is close to Generate
   immediately; the campaign questions mostly steer the interview and
   fill occasion-specific gaps.
5. **Message + Generate** — unchanged (message card lane, then
   `/generate` with the campaign slug). The render wears campaign X's
   theme/copy; the old video stays in the gallery.

## Answer meaning across campaigns

Answers are ephemeral steering priors (they seed the interview; the
conversation is what gets mined into memories). So asking related
questions on a new occasion is not duplication — new answers steer NEW
conversations, and everything already extracted stays in the graph. The
app never needs to merge answer sets across campaigns; the agent keeps
them separate on purpose.

## Node checklist (small)

- Forward `person_id` on the tribute-campaigns proxy (targeting doc).
- Pass `campaign` in `session_metadata` on `POST /sessions` for
  campaign-entered sessions (the agent backstop-stamps at generate, but
  entry-time stamping makes the card copy right from turn one).
- `unlock_prepare` proxy: no change — new response field passes through.

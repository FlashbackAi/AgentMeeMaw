# Delta since the last handoff — tribute card / campaigns (2026-07-17)

You already have `TRIBUTE_NEW_CAMPAIGN_FLOW.md`, `TRIBUTE_VIDEO_GALLERY.md`
and `TRIBUTE_CAMPAIGN_TARGETING.md`. This is ONLY what's new or newly
verified since — read this instead of re-reading those.

## NEW since the docs you received

1. **Hard rule added — a generated video always wins the card.**
   If a tribute for the active campaign is `complete` with a `video_url`,
   the card shows THE VIDEO (play/share/download + "Make another").
   The questions flow must be unreachable in that state. Full state
   machine now at the top of `TRIBUTE_NEW_CAMPAIGN_FLOW.md`.

2. **`tribute_answered` now prefills after completion too.**
   Previously it only reflected an OPEN tribute. Now, when the campaign's
   last tribute is complete, `unlock_prepare` returns THAT tribute's
   answers. Contract for "Make another": don't show the modal — pass
   `tribute_answered` straight through as `archetype_answers` on
   `/session/start`. (Agent commit `cc91753`; rides the next agent
   deploy — until then the field is simply empty after completion, so
   code defensively: empty → fall back to showing the modal.)

3. **Regenerate now honors CRM edits** (agent-side, deployed): theme/
   campaign changes reach regenerated videos. No app change — just stop
   telling users it doesn't work.

## STILL PENDING from the earlier docs — verified against prod data today

We watched real sessions in the prod DB; these gaps are confirmed live:

- [ ] **Card is not campaign-skinned.** It says "start your tribute";
      it must use the campaign's `display_name` ("To My Partner in
      Crime") and its `message_card_copy` on the message slot.
- [ ] **Answers never commit.** Today's sessions saved 1–2 answers as
      DRAFTS and no session was started — committed answers require
      `archetype_answers` in `session_metadata` on `POST /sessions`.
      Drafts alone are resume-state, not answers.
- [ ] **No completed/gallery state.** Completed videos exist in
      `tribute_status` (one row per campaign, `campaign_slug` +
      `video_url` columns are live) but the card restarts the funnel.
- [ ] **Node: two one-liners** — add the campaign columns to the
      tribute-status SELECT (gallery doc §Node) and forward `person_id`
      on the tribute-campaigns proxy (targeting doc §Node).

## NO ACTION NEEDED

- 15-question Friendship Day bank, campaign name, funny message copy —
  all live in prod config already; they appear automatically once the
  card is campaign-skinned.
- Per-campaign answers/meter, stale-question fix, targeting enforcement —
  agent-side, deployed.

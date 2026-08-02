# Frontend Prompt — persist SKIPPED campaign archetype questions

**For:** the frontend (legacy repo) team.
**Bug:** the archetype questions asked before starting a campaign are **asked
again on the next campaign entry if the user skipped them** (in the archetype
modal, not chat).
**Agent status:** no behavior change needed. The one enabling change shipped
(`2710868`): `/themes/{id}/archetype_progress` now accepts `question_text` on
each answer (it was `extra="forbid"` before). The fix itself is frontend.

---

## Root cause

A skip is **never persisted.** `tribute_answered` (the committed answers
`unlock_prepare` returns) contains only *answered* questions — each with its
`option_*` and `question_text`. There are **zero skip entries** (verified across
prod tributes). And the "already asked?" filter is purely presence-based:

`unansweredCampaignQuestions` keeps a question iff its normalized `question_text`
is **not** found in `tribute_answered`. A skipped question was never written, so
its text isn't there → it re-shows. (The agent's `next_step` coverage uses the
exact same rule, so this is consistent on both sides.)

So the filter is fine — it's just never told the user skipped.

## The fix (frontend)

When you **commit** archetype answers, include the **skipped** questions too —
not just the answered ones. Each skip is:

```jsonc
{ "question_id": "q3", "question_text": "What food is your friendship?", "skipped": true }
```

The **`question_text` is the match key and must be present.** With it, the skip
lands in `tribute_answered`, and `unansweredCampaignQuestions` filters it out on
re-entry — no re-ask.

Where to send it (both paths now accept `question_text`):
- **Commit** — `session_metadata.archetype_answers` on `POST /session/start`
  (this is a free dict; it already carries `question_text` for answered ones —
  just add the skipped ones the same way).
- **Draft / resume** — `POST /themes/{id}/archetype_progress` answers. This is
  the model that previously rejected `question_text` (422). Fixed in `2710868`;
  you can now include it (and the skip entries) on drafts too, so a resumed
  draft doesn't lose the skips.

## Do / don't

- **Do** match/persist by `question_text` (normalized: trim, collapse
  whitespace, lowercase). It's stable across campaign version edits.
- **Don't** match by `question_id` — ids differ across campaign versions (that's
  the whole reason `unansweredCampaignQuestions` uses text). Sending `question_id`
  is fine as metadata, but it's not the match key.
- **Don't** worry about the meter — `answered_layers` (the campaign %)
  **deliberately excludes pure skips** (a skip is not a captured lead), so
  persisting skips does **not** inflate the percentage. It only suppresses the
  re-ask. Correct and intended.

## Verify

1. Start a campaign, **skip** 3 of N questions, answer the rest, commit.
2. `tribute_answered` on the next `unlock_prepare` contains all N — the 3 skips
   as `{question_id, question_text, skipped: true}`.
3. Re-enter the campaign → the 3 skipped questions are **not** re-asked; the
   modal shows only genuinely-unanswered ones (or none → auto-finalizes).
4. The campaign % is unchanged by the skips (only real answers/leads move it).

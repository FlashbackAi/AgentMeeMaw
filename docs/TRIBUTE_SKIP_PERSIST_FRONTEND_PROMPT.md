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

## Node changes — only if your proxy is not transparent

The skip data makes two round-trips through Node; both must preserve
`question_text` **and** `skipped`. If Node forwards these payloads opaquely
(most likely), **no Node change**. If Node validates/whitelists fields, open the
gate for those two keys:

1. **Answers → agent (request):**
   - `POST /session/start` — the skips ride inside
     `session_metadata.archetype_answers`. `session_metadata` is a free-form
     blob on the agent, so a passthrough proxy needs nothing. Only if Node
     reshapes the answer objects: allow `question_text` + `skipped`.
   - `POST /themes/{id}/archetype_progress` — the `answers[]`. Same: if Node
     schema-checks each answer, allow `question_text` + `skipped` (the agent's
     model now accepts them, `2710868`).
2. **`unlock_prepare` → frontend (response):** the agent returns
   `tribute_answered` with each entry carrying `question_text` + `skipped`
   (plus `next_step` / `archetype_complete`). Passthrough → nothing. If Node
   maps the response to a frontend shape, forward those fields and **keep
   `question_text` on every `tribute_answered` entry** — it's the match key; drop
   it and the re-ask returns.

Net: transparent proxy → no Node work. Field-whitelisting proxy → allow
`question_text` + `skipped` through in both directions.

## Verify

1. Start a campaign, **skip** 3 of N questions, answer the rest, commit.
2. `tribute_answered` on the next `unlock_prepare` contains all N — the 3 skips
   as `{question_id, question_text, skipped: true}`.
3. Re-enter the campaign → the 3 skipped questions are **not** re-asked; the
   modal shows only genuinely-unanswered ones (or none → auto-finalizes).
4. The campaign % is unchanged by the skips (only real answers/leads move it).

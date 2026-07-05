# Node Prompt — Multi-select onboarding + archetype questions

**For:** the Node Backend + frontend team.
**Status:** agent side built (onboarding + theme-unlock surfaces). The Node
backend validation (`OnboardingController`, `ThemesController` doc comment)
has already been updated alongside the agent change — **the remaining work
is frontend UI**: chips become toggles, and the answer wire shape gains an
array form.
**No migration, no new queue, no new endpoints.** The old single-choice
wire shape stays accepted everywhere, so this ships incrementally with zero
risk to the current flow.

---

## TL;DR

Every onboarding and archetype question is now **multi-select**: the user
can tap **any number of chips** and **also** type their own words on the
same question. Two exceptions stay single-choice: the two ground-truth
onboarding questions (`gt_region`, `gt_birth_era`) — each writes exactly one
value into the subject's ground truth, so two answers can't be stored.

Wire changes, in one line each:

1. **Questions** (both `GET /onboarding/archetype-questions` and theme
   `unlock_prepare`) now carry **`allow_multiple: boolean`** — render
   toggle chips when `true`, radio behavior when `false`.
2. **Answers** gain **`option_ids: string[]`** (theme side also
   **`option_labels: string[]`**), and chips may **combine with
   `free_text`**. The legacy single `option_id` / `option_label` is still
   accepted and treated as a one-element list.
3. **`skipped: true` stands alone** — a skipped answer carrying chips or
   free text is a `422`.

---

## 1. Onboarding flow

### `GET …/onboarding/archetype-questions` — new flag

```jsonc
{
  "questions": [
    {
      "id": "friend_usual_activity",
      "text": "What did you usually do together?",
      "allow_free_text": true,
      "allow_skip": true,
      "allow_multiple": true,          // NEW — toggle chips
      "options": [ { "id": "talk", "label": "Talk for hours" }, … ]
    },
    {
      "id": "gt_region",
      "allow_multiple": false,         // ground-truth pair stays single-choice
      …
    }
  ]
}
```

### `POST …/onboarding/archetype-answers` — new answer shape

```jsonc
{
  "answers": [
    {                                   // multi: chips + own words together
      "question_id": "friend_usual_activity",
      "option_ids": ["talk", "eat"],
      "free_text": "played carrom on Sundays"
    },
    { "question_id": "gt_region", "option_ids": ["south"] },  // single-choice: exactly one
    { "question_id": "friend_kind", "option_id": "funny" },   // legacy shape — still valid
    { "question_id": "gt_birth_era", "skipped": true }
  ]
}
```

Validation matrix (enforced by both Node and the agent — the backend
changes are already in):

| shape | multi question | `allow_multiple: false` question |
|---|---|---|
| several chips | ✅ | ❌ `422` "single option" |
| chips + `free_text` | ✅ | ❌ `422` "exactly one of" |
| one chip only / free text only | ✅ | ✅ |
| `skipped` + anything else | ❌ `422` | ❌ `422` |
| nothing at all | ❌ `422` (pick, type, or skip) | same |

Everything else is as before: one answer per returned question, exactly
once, 3–12 answers, `free_text` ≤ 500 chars, unknown `question_id` /
`option_id` → `422`.

**What the agent does with a multi answer** (context, nothing for you to
build): the selected options' coverage/entity implications merge — coverage
dims union, entities dedupe, conflicting life-period estimates drop — and
the first-session opener sees all of it, e.g. *"What did you usually do
together? Talk for hours, Eat together — and in their own words: 'played
carrom on Sundays'."* More chips = a richer opener, which is the point of
the feature.

## 2. Theme unlock / tribute flow

Same pattern on the three touchpoints:

- **`POST /themes/{id}/unlock_prepare`** — every returned archetype
  question now has `allow_multiple: true` (LLM-generated and the Father's
  Day bank alike). Render toggle chips + the free-text input together.
- **`POST /themes/{id}/archetype_progress`** (draft save) and
  **`session_metadata.archetype_answers`** on `POST /sessions` — entries
  gain `option_ids` + `option_labels` (labels matter here: the agent feeds
  them to the response generator verbatim):

```jsonc
{
  "question_id": "q2",
  "question_text": "When did cricket really take hold?",
  "option_ids": ["q2_o1", "q2_o3"],
  "option_labels": ["As a kid in the streets", "Coaching the next generation"],
  "free_text": "he umpired colony matches too"   // optional, rides along
}
```

- Skipping stays encoded as **omission** in `archetype_answers` (unlock)
  and as `skipped: true` in progress drafts — unchanged.
- **Draft resume:** `archetype_answers_draft` returned by `unlock_prepare`
  can now contain multi-shape entries. Restore *all* chips in
  `option_ids`, not just the first.

## 3. Frontend behavior spec

- **Chips toggle.** Tap adds, tap again removes. No cap beyond "all of
  them" (wire cap is 12 ids per answer; no question has more than ~6).
- **Hint the affordance.** Add a subtle "(pick any that fit)" next to
  multi questions — users trained on radio chips won't discover toggling
  otherwise.
- **Free text is additive**, not a replacement: placeholder like "Add your
  own words (kept alongside your picks)". Typing must NOT clear chips on
  multi questions. On the two GT questions keep today's behavior (free
  text clears the chip and vice versa).
- **Skip clears everything** for that question, and picking anything
  clears skip.
- **Send the array shape** (`option_ids`, one-element is fine) for new
  code paths. The legacy `option_id` keeps working, so you can migrate
  screen-by-screen.

## 4. What does NOT change

- Endpoints, auth, status codes, question counts (10 onboarding, 3–4 per
  theme), the 3–12 answers guard, the every-question-exactly-once rule.
- Ground-truth writes, coverage seeding, entity creation — all agent-side.
- Drafted/committed answers already stored in the old shape keep rendering
  fine; no backfill.
- `question_decision` chips, GT taps on `/turn`, and the tribute message
  sidecar — different surfaces, untouched.

## 5. Acceptance checklist

- [ ] Onboarding renders toggle chips for `allow_multiple: true`, radios
      (or single-select behavior) for the GT pair.
- [ ] User can pick several chips AND type free text on one question; the
      submit body carries `option_ids` + `free_text` together.
- [ ] GT questions still enforce one-of client-side (server 422s
      otherwise).
- [ ] Skip is exclusive both ways (picking clears skip, skip clears picks).
- [ ] Theme unlock modal: chips toggle; `option_labels` sent alongside
      `option_ids`; draft resume restores multiple chips.
- [ ] Regression: an unmodified old client (single `option_id`) still
      completes onboarding end-to-end.
- [ ] Opener sanity check: multi-pick + free text onboarding produces an
      opener referencing more than one of the picked details.

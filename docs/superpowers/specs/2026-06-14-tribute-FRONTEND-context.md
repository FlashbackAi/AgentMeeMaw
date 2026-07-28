# Frontend Context — Tribute Output (Father's Day)

**Audience:** the Frontend team. **Author:** agent service. **Date:** 2026-06-14.

This is **context, not a design spec.** The tribute feature is mostly the
**existing UI patterns you already ship**, pointed at a new flow. This doc
tells you which existing surfaces to reuse and the exact data they bind to.
**New visual decisions (the meter's look, the result/share screen, the
campaign hero) are yours** — this only pins the pieces that reuse what's
already built and the data contract you can't change.

> Transport note: the frontend talks to **Node**, as today. Everything
> below is data Node already has (it reads the agent's Postgres views and
> proxies the agent's `/session/start` + `/turn`). Nothing here changes how
> you talk to Node — it adds fields to responses you already consume.

---

## The shape of the flow (all existing surfaces)

```
[theme/lock card]  →  [archetype MC modal]  →  [chat]  →  ["make my video" + result]
   (campaign hero)      (just MORE questions)    (taps + meter ride along)
```

Every box is something you already render. Here's what changes in each.

---

## 1. Entry — a featured "theme card" (existing pattern)

The tribute is presented like the **theme lock cards** you already render
on the legacy/profile grid. During the Father's Day window it's just
**featured first**. Node tells you which campaign is active and its copy:

```json
// Node surfaces this from GET /tribute-campaigns
{ "active_featured_slug": "fathers_day_2026",
  "display_name": "A Letter to Dad",          // use as the card title
  "active_start": "2026-06-01", "active_end": "2026-06-22" }
```

- **Reuse:** the existing locked-theme card component.
- **Copy is supplied** (`display_name`) — don't hardcode "Father's Day".
- **Yours to decide:** whether/how to make it a bigger "hero" during the
  window vs. a normal card. The only fixed thing is *use the supplied
  display name* and only feature it when a campaign `is_active`.

---

## 2. The multiple-choice questions (existing archetype unlock modal)

The tribute is a real theme now (seeded for every legacy), so it shows up
in the theme grid like any other — tapping its card opens the **same
archetype-unlock MC modal you already have** (the one universals/emergent
themes use). The ONLY differences:

1. **More questions** — 6–8 instead of the usual 3–4. Your modal already
   renders an arbitrary list; just don't assume exactly 3–4.
2. **Father's Day framing** in the question text (comes from the agent —
   you render whatever text arrives).

Data is the **identical shape** you already consume from
`POST /themes/{id}/unlock_prepare`:

```json
{
  "archetype_questions": [
    { "question_id": "q1",
      "text": "What did he carry that you still carry?",
      "options": [ {"option_id": "q1_o1", "label": "His patience"}, ... ],  // 4 chips
      "allow_skip": true, "allow_free_text": true }
    // ... 6–8 of these
  ],
  "archetype_answers_draft": [ ... ]   // resume a half-finished modal (existing)
}
```

- **Reuse:** chips + free-text + skip per question; the resumable-draft
  behavior (persist partial answers, restore on reopen) — **all already
  built** for theme unlock.
- **Yours to decide:** nothing new. If anything, only how a 6–8 question
  modal paces (e.g. one-per-screen vs. scroll) — and only if your current
  modal feels long; that's a polish call, not a requirement.

On finish, you hand the answers to Node exactly as you do for theme unlock;
Node starts the session with them.

---

## 3. The chat (unchanged)

Straight into the **normal chat surface** — same `/session/start` opener +
`/turn` loop you already render. Two things ride along *inside* responses
you already parse:

### 3a. Tap cards under the reply (existing tap-card pattern) — one new kind

You already render **tap cards** beneath the assistant reply (coverage taps,
ground-truth taps): a short question + tappable chips + free-text + skip.
The tribute flow adds **one new tap kind, `"message"`**, that renders with
that **same card** — it's the "say it to him" moment:

```json
// inside /turn metadata.taps[]
{ "kind": "message", "question_id": null,
  "text": "Fathers and sons don't always say it out loud. If he could hear one thing from you right now — what is it?",
  "options": [], "field": null }
```

- **Reuse:** the existing tap-card component (free-text + skip). `options`
  is usually empty for this kind → free-text is the primary input.
- **Sending the answer:** same pattern as the ground-truth tap — the user's
  text goes back on the **next `/turn`** as a sidecar (Node handles the
  exact field; you give Node the text + a skipped flag).
- **Already true and unchanged:** for any tap with `question_id: null`
  (this one included), **do not** send the Skip/Suppress/Defer
  `question_decision` chip-row — that's only for producer-bank questions.
  Your existing code already branches on `question_id` presence.
- **Copy is supplied** (`text`) — Father's Day wording arrives from the
  agent; render it verbatim.

### 3b. The live completion meter (NEW surface — visual is YOURS)

Every `/turn` response now carries, while in the tribute flow:

```json
"tribute_progress": {
  "percent": 60,
  "ready": false,
  "title": "A Letter to Dad",
  "next": "message",
  "slots": [
    {"key": "memories",   "label": "Shared memories",    "hint": "Tell three stories about a time with them.",        "filled": false, "count": 2, "target": 3},
    {"key": "message",    "label": "Your message",       "hint": "If he could hear one thing from you — what is it?", "filled": false, "count": null, "target": null},
    {"key": "appearance", "label": "How they looked",    "hint": "A few details so we can picture them.",             "filled": true,  "count": null, "target": null},
    {"key": "signature",  "label": "What made them them","hint": "A saying, a habit, or a trait of theirs.",          "filled": true,  "count": null, "target": null}
  ]
}
```

- This is the **"how far to my video / what's left / what do I do next"** signal.
- `percent` is monotonic within a session (never goes backward). It can lag
  a beat — memory/appearance/signature slots flip *after* extraction
  finishes, so update again when Node tells you (or just re-read on the next
  turn). `message` flips immediately.
- **`title`** is the campaign-skinned meter header (e.g. "A Letter to Dad" in
  the Father's Day window, "A Tribute" otherwise) — use it for the header
  label instead of a hardcoded "YOUR TRIBUTE".
- **`next`** is the key of the first unfilled slot (or `null` at 100%). Drive
  the "next — …" steer from this slot's `hint`, so the prompt is always
  actionable and never a guess. When `next` is `null`, show the ready/generate
  state instead.
- **`label`** is the short slot name (the "what's left" checklist); **`hint`**
  is the actionable one-liner telling the user *how* to fill that slot — both
  display-ready, render as-is. `hint` for the `message` slot carries the
  campaign voice; the rest are skin-neutral.
- **`count`/`target`** give granular progress for the memories slot only
  (e.g. 2 of 3 stories → render "one more story"); they are `null` on every
  other slot, which is purely filled/unfilled.
- **Entirely your design:** bar vs. ring vs. checklist, where it lives,
  animation. The only contract is the JSON above.

---

## 4. "Make my video" + the result (data fixed, screens YOURS)

- When `tribute_progress.ready` is `true` (100%), enable a generate action.
  Node calls the agent; you just need to know **`ready` gates the video**
  (the storybook can generate a bit earlier — Node will tell you).
- After generating, the artifact renders **asynchronously** (Node's worker
  builds it). Poll the tribute row Node exposes for **URL presence**:
  - `video_url` → the tribute video is ready,
  - `image_url` / `thumbnail_url` → the storybook is ready.
  Treat URL-present as "done" (same as how existing per-record artifacts
  appear once their URL lands).
- **Entirely your design:** the generate CTA, the "we're making it…"
  waiting state, and the **share/result screen** (this is the whole point —
  make it good enough to post). Nothing about that screen is fixed by the
  contract.

---

## 5. What's FIXED vs. YOURS — quick table

| Surface | Reuse / fixed | Your call |
|---|---|---|
| Featured entry card | locked-theme card component; use supplied `display_name`; only feature when `is_active` | hero treatment, placement emphasis |
| Archetype MC modal | existing unlock modal; chips + free-text + skip; resumable draft; render supplied text | pacing if 6–8 feels long |
| Chat | unchanged | — |
| Message tap | existing tap-card component; render supplied `text`; no `question_decision` for null `question_id` | nothing |
| Completion meter | bind to `tribute_progress` JSON; `title`/`label`/`hint` display-ready, `next` drives the steer, `count`/`target` for memories | the entire visual |
| Generate + result | `ready` gates video; poll URL presence for "done" | CTA, waiting state, share/result screen |

---

## 6. Things NOT to build (out of scope / handled elsewhere)

- **No voice/TTS of the subject** in v1 (the message is text-on-screen in
  the video). An optional *contributor* voiceover, if you ever want one, is
  a separate future call.
- **The video/storybook rendering itself** is Node's job (it reads the
  agent's composed prompts and calls the generation model) — you only
  display the finished URL.
- **Don't hardcode "Father's Day"** anywhere — all campaign copy
  (card title, the message-tap question) arrives from the backend so the
  same screens work year-round and for future campaigns.

---

## 7. No special entry — it's just a theme

The tribute is **seeded as a real theme on every legacy**, so getting from
the featured card into the MC modal is the **exact theme-unlock path you
already build**: the tribute appears in the theme grid (Node reads it from
`active_themes_with_tier`, `kind='tribute'`), you open it via the same
`unlock_prepare` modal, and start the session the same way. No new endpoint,
no special case — if your theme-unlock flow works today, this works.

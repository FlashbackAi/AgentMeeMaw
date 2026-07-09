# Node Prompt — Question feed → tap to start a conversation

**For:** the Node Backend team.
**Status:** agent side built + merged to `main` (branch `feat/question-feed`,
merge `f07f4cf`). No agent migration required — reads existing tables.
Two Node changes outstanding: render the feed, and pass the tapped
question's id on session start.

## Why

We already build a per-person bank of questions we'd like to ask about a
legacy (the "producer bank": dropped references, underdeveloped people/
places, thread deepeners, life-period gaps, universal dimensions). Until
now those only surfaced implicitly — the bot picked one to open with or
weave into a reply. There was no way for the contributor to **browse**
them.

New behaviour: show those questions in the scrolling feed. When the
contributor taps one, start a conversation whose opener is anchored on
that exact question. Everything the agent needs already exists — Node
just renders a new read and forwards one id.

## The two changes

### 1. Render the feed — `GET /questions/feed`

New agent endpoint. It returns a ranked, ready-to-render list; **do not
re-sort or re-filter it** — the ranking, decision-filtering, and
diversity spread are agent-side on purpose.

```
GET /questions/feed?person_id=<uuid>&limit=<int, default 25, max 50>
X-Service-Token: <SERVICE_TOKEN>
```

**Response 200**

```jsonc
{
  "questions": [
    {
      "question_id": "uuid",
      "text": "string",                 // render this
      "source": "dropped_reference | underdeveloped_entity | thread_deepen | life_period_gap | universal_dimension",
      "themes": ["family"],             // optional chips/grouping, if you want
      "created_at": "iso-8601"
    }
  ]
}
```

- **Producer-bank only.** Coverage-tap and archetype/unlock questions
  never appear here — they have their own surfaces.
- **Already ordered** by the same signal the bot uses to pick what to ask
  next (source priority + recency + defer boost). Render top-to-bottom.
- **Already filtered.** Questions the contributor chose to `skip` or
  `suppress` are gone; `deferred` ones float up. You do not apply
  `question_decisions` yourself for this surface.
- **Empty is normal.** A fresh legacy with no bank yet returns
  `{ "questions": [] }` with a `200` — render an empty/blank state, not an
  error.
- `source` is a stable slug (part of the contract) — safe to switch on for
  an icon or label. `themes` is optional decoration.

### 2. Start a seeded session — `session_metadata.question_id`

When the contributor taps a feed question, start a session exactly as you
do today, adding **one optional field** to `session_metadata`:

```jsonc
POST /session/start
{
  "session_id": "uuid",
  "person_id": "uuid",
  "role_id": "uuid",
  "session_metadata": {
    "question_id": "uuid"          // NEW — the question_id from the feed
  }
}
```

- The agent loads that question and the opener **anchors on it**.
- An explicit tap is always honored — it overrides whatever the bot would
  have auto-opened with, in both starter and steady phase.
- Unknown / wrong-person / inactive `question_id` is **ignored** — the
  agent falls back to a normal opener. Safe to send optimistically.
- It **composes** with `theme_id`: if you're already passing a theme, you
  may pass both.
- Works on the streaming twin too (`POST /session/start/stream`), same
  field.

That's the whole write path. There is no "mark question answered" call —
the agent records the seed internally, and the question resolves naturally
once the contributor talks and extraction runs.

## What Node does NOT need to change

- **No new auth, no new headers.** Same `X-Service-Token` as every other
  agent call.
- **No Postgres reads for this.** You *could* still read `active_questions`
  raw for other surfaces, but the **feed** must come from
  `GET /questions/feed` — don't rebuild the ranking against the view, it
  will drift from the bot.
- **No schema changes, no migration.** The endpoint reads existing tables.
- **No queue changes.**
- **Response/metadata shape of `/session/start` is unchanged.** You read
  `metadata.selected_question_id` / `question_chips` exactly as today; when
  you seed a feed question, `selected_question_id` simply comes back as the
  one you tapped.

## Acceptance check

1. `GET /questions/feed?person_id=<a legacy with some history>` returns a
   ranked, non-empty `questions[]`; a brand-new legacy returns `[]` with
   `200`.
2. Tap a question → `POST /session/start` with that `question_id` in
   `session_metadata` → the opener clearly opens **on that question's
   topic**, not a generic greeting.
3. Send a bogus `question_id` (random uuid) → session still starts with a
   normal opener, no error.
4. A question the contributor previously `suppress`ed does **not** reappear
   in the feed.

## Reference

Full contract: `API.md` §7d (`GET /questions/feed`) and §5
(`POST /session/start` → `session_metadata.question_id`). Integration
notes: `NODE_INTEGRATION.md` §6.4.

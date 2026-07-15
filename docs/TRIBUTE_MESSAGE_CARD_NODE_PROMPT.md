# Work order: tribute message card — finish the video without a chat

**For:** the Node backend repo (`backend-services/legacy`) + a short
frontend contract for the consumer app. **From:** the Python agent
service — agent-side is DONE (branch merged 2026-07-15).

## The product change (one paragraph)

Today the "say one thing straight to them" message can only be answered
inside a chat session, and once it's the only missing slot the chat card
re-asks every couple of turns (naggy, and typed answers were ignored).
Now: when a tribute's meter shows the **message as the only unfilled
slot**, the tribute card itself asks the question directly — text box,
submit, meter flips to 100%, **Generate button appears**. No chat. The
question text is relationship/campaign-aware and comes from the agent.

## Node: one new proxy route

`POST /api/v2/legacy/persons/:personId/tributes/:tributeId/message`
→ agent `POST /tributes/{tributeId}/message` with body
`{ person_id, text }` (`text` 1–2000 chars). Standard person-role gate
like the other tribute routes; no admin token.

Response = the agent's fresh **progress payload** (identical shape to
`GET /tributes/{id}/progress`) — pass it through verbatim.

| Agent status | Meaning for the FE |
|---|---|
| `200` | Message saved; body is the new progress (percent/ready/slots). |
| `404` | Wrong person / unknown tribute. |
| `409` | Tribute already rendered (`complete`) — hide the card. |
| `422` | Empty or >2000-char text. |

## Progress payloads: one enrichment, zero shape changes

`GET /tributes/{id}/progress` and the `tribute_progress` block on `/turn`
are unchanged in shape. The `message` slot's `hint` now carries the fully
resolved question (campaign copy → relationship-profile copy → neutral) —
**render that string as the card's question**, don't hardcode copy.

## Frontend contract (consumer app)

1. Wherever the tribute meter renders: when `ready === false` and the
   only unfilled slot is `message` (equivalently `next === "message"` and
   every other slot `filled`), show the finish card:
   - Question = the `message` slot's `hint`.
   - Multiline text input (cap 2000 chars) + one submit button
     ("Add my message").
   - Optional helper: "This becomes the emotional heart of the video."
2. On submit → the new Node route → replace the meter with the returned
   progress. It will now be `percent: 100, ready: true` → reveal the
   existing **Generate** button (same generate flow as today; nothing
   auto-renders).
3. Re-submitting before generate is allowed and simply replaces the
   message (no error state needed).
4. `409` → the tribute is already rendered; hide the card and show the
   video instead.

## Chat behavior changes (nothing for Node to do, just expect it)

- The in-chat message card now fires **at most once per session** (its
  warm story moment). The every-2-turns re-ask is gone — the tribute card
  above owns the "last slot" case permanently.
- If a user types their message as a normal chat reply right after the
  in-chat card was offered, the agent now captures it (one-shot LLM
  check) instead of ignoring it — the next turn's `tribute_progress`
  will show the message slot filled. No new fields, no FE work.

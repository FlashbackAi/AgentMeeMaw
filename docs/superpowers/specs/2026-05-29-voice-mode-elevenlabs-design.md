# Voice Mode — ElevenLabs ConvAI integration

**Status:** Design
**Date:** 2026-05-29
**Scope:** Adds a real-time voice conversation mode to Legacy Mode using
ElevenLabs Conversational AI for audio I/O + STT + TTS + turn-taking,
with this repo's `/turn/stream` as the brain. Existing text-mode `/turn`
and `/turn/stream` paths are **unchanged** and **not deprecated**.

---

## 1. Goal

Give contributors a Gemini-Live–quality conversation experience over voice:
they speak naturally, the agent speaks back with warmth and natural prosody,
interruption works, and the underlying Flashback agent (intent classifier,
retrieval, response generator, segment detector, extraction worker) keeps
running exactly as it does in text mode.

**Voice mode = screen + voice, not audio-only.** The browser still renders
the chat surface — transcript, question chips, tap cards, archetype
modals all appear on screen during a voice session. Audio is an
additional input/output channel layered on top of the existing chat UI.
The contributor can tap chips, fill modals, and read the transcript
while also speaking and hearing the agent. This is the Gemini Live
mental model, not a phone call.

Non-goals:

- **Cloning the subject's voice.** Per CLAUDE.md §1 we do not clone voices.
  TTS uses a stock ElevenLabs voice that fits the archivist tone.
- **Replacing text mode.** Text and voice are siblings; the user picks per
  session.
- **Live transcription UI.** The voice client may render the running
  transcript for accessibility, but it is not a spec requirement.

## 2. High-level architecture

The browser opens two parallel channels: ConvAI for audio I/O, and the
existing Node chat surface for the rendered transcript + chips +
cards. Both observe the same conversation through different surfaces.

```
                          ┌──────────────────────────────────┐
                          │  ElevenLabs ConvAI               │
                          │  STT + VAD + turn-taking +       │
   ┌────────────────────▶ │  barge-in + v3 TTS               │
   │  WS audio I/O        └──────────┬───────────────────────┘
   │                                 │ POST /chat/completions
┌──┴──────┐                          │ (OpenAI SSE shape)
│ Browser │                          ▼
│         │             ┌── Node adapter: /voice/llm/chat/completions
│ (chat   │             │   reads dynamic_variables off the request
│  UI +   │             │
│  audio) │             │
└────┬────┘             │   POST /turn/stream {mode:"voice", ...}
     │                  │    ─────────────────────────────▶ ┌──────────────┐
     │ chat SSE/WS      │    SSE: meta / text_delta / done   │ THIS REPO    │
     │ (chips, taps,    │    ◀──────────────────────────────│ (agent svc)  │
     │  transcript)     │                                    └──────────────┘
     ▼                  │
┌────────────┐ fan-out:  │
│   Node     │ ┌─ chips/taps/transcript ─▶ chat channel
│  (separate │ └─ text_delta rewrap ─────▶ ConvAI as OpenAI SSE
│   repo)    │                                    │
│            │                                    ▼
│            │ mint signed URL +              ConvAI streams v3 TTS
│            │ inject dynamic_variables       audio back to Browser
└────────────┘ {session_id, person_id,
               role_id, mode:"voice"}
```

Service boundaries:

- **Browser ↔ ConvAI** carries audio. Auth is a short-lived signed URL
  minted per session by Node. The ElevenLabs API key never leaves Node.
- **Browser ↔ Node** carries the rendered chat surface — transcript,
  chip rows, tap cards, archetype modals. Same channel as text mode.
- **ConvAI ↔ Node adapter** is the LLM webhook. ConvAI speaks OpenAI's
  `/chat/completions` SSE protocol; the Node adapter is the only place
  that protocol exists in our stack.
- **Node adapter ↔ this repo** is the existing `/turn/stream` SSE
  contract, called with `mode="voice"`. Node fans the response
  out two ways: `text_delta` events get re-wrapped as OpenAI SSE chunks
  for ConvAI (which feeds TTS), and `meta` events (chips, taps) get
  forwarded to the browser's chat channel for rendering.

This repo never speaks to ElevenLabs and never speaks OpenAI shape.

## 3. Streaming end-to-end

Every leg except one is streamed:

| Leg                              | Streaming? | Notes                                                                        |
| -------------------------------- | ---------- | ---------------------------------------------------------------------------- |
| Browser mic → ConvAI             | yes        | WebRTC/WS audio                                                              |
| ConvAI STT (internal)            | yes        | Streaming transcription                                                      |
| **ConvAI → Node adapter**        | **no (request) / yes (response)** | One-shot POST with full transcript once user finishes; response is SSE |
| Node adapter → `/turn/stream`    | yes        | Existing SSE                                                                 |
| `/turn/stream` → Node adapter    | yes        | `text_delta` events as the LLM emits tokens                                  |
| Node adapter → ConvAI            | yes        | OpenAI SSE chunks                                                            |
| ConvAI TTS (internal)            | yes        | Streams audio as soon as text deltas arrive                                  |
| ConvAI → Browser audio playback  | yes        | First audio ~hundreds of ms after the first token                            |

The one non-streaming hop is by design: the brain doesn't run until the
user is done speaking (VAD-detected end of turn). That matches Gemini
Live and every other realtime voice agent.

## 4. Changes in this repo

All additive. No existing field, route, or behavior changes.

### 4.1 `mode` request field

Add to both `TurnRequest` and `SessionStartRequest`:

```python
mode: Literal["text", "voice"] = "text"
```

Plumbed through the orchestrator into `OrchestratorState.mode` and into
working memory hydration on `/session/start` so it survives across turns
in the same session.

### 4.2 Response Generator: voice prompt variant

A new prompt fragment in the Response Generator activated when
`state.mode == "voice"`. Applies to **all** response-generator entry
points used by `/turn/stream` and `/session/start/stream`:

- `stream_turn_response`
- `stream_first_time_opener`
- `stream_starter_opener`

Voice prompt rules, applied as additional instructions on top of the
existing intent-specific prompt:

1. **No markdown.** No `**bold**`, no `_italic_`, no headers, no bullets,
   no code fences. Markdown gets read aloud literally by TTS.
2. **Conversational register.** Contractions, short sentences, natural
   rhythm. Voice-first; never narrate the UI ("above," "below," "tap,"
   "see") — the contributor sees the chips/cards on their own.
3. **Sparing disfluencies.** `well…`, `you know`, `um`, `mm` — used at
   thoughtful moments, not as filler. Aim for one disfluency every
   2–4 sentences at most.
4. **ElevenLabs v3 audio tags allowed inline.** Bracketed tags the v3
   model converts to paralinguistic sound. Whitelist for Voice Mode:

   - `[chuckles]` — small warm laugh, recalling something fond
   - `[softly]` — emotional moments, grief, tenderness
   - `[warm]` — affectionate framings
   - `[thoughtful]` — pause before a careful question
   - `[gentle pause]` — brief beat for absorbing what was said
   - `[curious]` — leaning into a follow-up
   - `[sighs]` — sparingly, only when the moment genuinely lands there

   Tags lean **warm and contemplative**. This is legacy work; avoid
   playful or comic tags (`[laughs hard]`, `[teasing]`, `[surprised]`).
   Use one tag per response at most, none is fine.

5. **Length.** Voice-mode replies trend slightly shorter than text —
   one or two short paragraphs, not three. Long answers feel
   monologuey when spoken.
6. **Tap-pending override.** When a `<tap_pending>` block is present,
   the default acknowledgment-only behavior (text mode: "the chip IS
   the next question, don't ask one yourself") does **not** apply in
   voice mode. The agent **speaks the tap question** naturally as part
   of its reply. The chip card still renders on the contributor's
   screen as a parallel affordance — they can tap or speak the answer.
   This is the fix for what would otherwise be a dead-air bug: in text
   mode the tap card was the next question; in voice the agent has to
   *say* the question even though the chip is also visible.

   This rule is mode-neutral by design: wherever taps are surfaced
   (currently `select_coverage_tap` in starter phase + `promote_seeded_to_tap`
   for promoted starter-phase questions; potentially extended to
   steady phase later), voice mode handles them by speaking.

The prompt fragment is a single template appended at the end of the
existing system prompt. Behind the scenes it remains one model, one
call; only the instructions differ.

### 4.3 Working Memory

Persist `mode` alongside `phase`, `coverage_state`, etc. on the WM
session record. Set on `/session/start`, read on every `/turn`.
Defaults to `text` for sessions started before this change rolls out.

### 4.4 Things that explicitly do not change

- **Tap card / question chip emission.** `meta` events still carry
  `taps` and chips. The browser renders them on the chat surface in
  voice mode just like in text mode — voice mode is screen + voice,
  not audio-only (see §1). The only mode-specific behavior is the
  Response Generator's tap-pending override (§4.2 rule 6), which makes
  the agent *speak* the question instead of going acknowledgment-only.
- **Intent classifier.** No mode-awareness. Same prompts, same outputs.
- **Retrieval.** Mode-agnostic.
- **Segment detector, extraction worker, thread detector, trait
  synthesizer, profile summary.** All mode-agnostic. The whole point of
  voice mode is that the legacy graph keeps building exactly as today.
- **Question producers, phase gate, coverage tracker.** Unchanged.
- **Existing JSON `/turn` and `/session/start` endpoints.** Unchanged.
  Callers that don't pass `mode` get `text`.

### 4.5 Token granularity (decided during implementation, not now)

The current `/turn/stream` emits one `text_delta` per Anthropic SDK
chunk — token-level. ElevenLabs v3 TTS accepts arbitrarily small
chunks, but sentence-aligned chunks produce slightly better prosody.
Two options:

a. **Pass through unchanged** (likely fine). Voice client + ConvAI handle
   reassembly.
b. **Sentence-buffer in the orchestrator** when `mode == "voice"`:
   accumulate deltas, flush on `.`, `!`, `?`, `\n`, or 200ms idle.

Decision deferred. Measure p50 first-audio latency under (a). If
prosody artifacts surface, add (b).

## 5. Node-side responsibilities (separate repo — informative)

This section is the contract Node implements. It is not built in this
repo.

### 5.1 Mint endpoint

Node exposes (to the browser):

```
POST /api/voice/session
body: { session_id, person_id, role_id }
```

Node:

1. Validates auth, authorizes the user against the legacy.
2. Calls our `/session/start` (JSON variant) with `mode="voice"`. The
   opener text comes back as a single string in the response body —
   Node holds it for step 4. The streaming variant
   `/session/start/stream` is also available if Node prefers to
   concatenate deltas, but JSON is simpler here since ConvAI needs the
   full string before the conversation starts.
3. Calls ElevenLabs to mint a signed conversation URL with
   `dynamic_variables`:

   ```json
   {
     "session_id":   "<uuid>",
     "person_id":    "<uuid>",
     "role_id":      "<uuid>",
     "mode":         "voice",
     "subject_name": "<person.display_name>"
   }
   ```

4. Passes the opener text into ConvAI's `agent_first_message` (or
   `first_message`) so ConvAI speaks it before the user says anything.
5. Returns `{ signed_url }` to the browser. Browser connects directly to
   ConvAI; the API key never reaches the client.

### 5.2 Adapter endpoint

Node exposes (to ElevenLabs ConvAI only — authenticated via shared
secret in headers or signed-URL pattern):

```
POST /voice/llm/chat/completions
```

OpenAI chat-completions request shape. Node:

1. Reads `session_id`, `person_id`, `role_id`, `mode` from the request's
   `dynamic_variables` (ConvAI forwards them).
2. Pulls the latest user message off `messages[]` (ConvAI builds the
   history, we don't need the full array — `/turn/stream` reads
   history from working memory).
3. Streams `POST /turn/stream` to this repo with
   `{session_id, person_id, role_id, message, mode:"voice"}`.
4. For each `text_delta` SSE event, emits an OpenAI-shape SSE chunk:

   ```
   data: {"id":"...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"<chunk>"},"finish_reason":null}]}\n\n
   ```

5. On the upstream `done` event, emits a terminal OpenAI chunk with
   `finish_reason:"stop"` and `data: [DONE]\n\n`.
6. Drops `meta` events from the upstream stream — ConvAI does not
   consume them. Node may still log them for observability.

### 5.3 Barge-in (audio only — no server-side abort)

When the user starts speaking over the agent:

1. ConvAI cuts TTS playback **locally on the audio side** — the user
   stops hearing the agent immediately. This is ConvAI's job and
   requires no work from Node.
2. ConvAI closes the SSE connection to the Node adapter for that turn.
3. **Node does NOT abort the upstream `/turn/stream` call.** The
   in-flight LLM completion runs to natural end, gets committed to
   working memory + `legacy_turns_v1` in full.

Explicit trade-off, made for simplicity in v1:

- The durable transcript will occasionally show the agent saying more
  than the user actually heard (whatever ConvAI muted at the audio
  cutoff). The intent classifier on the next turn sees the full prior
  reply as context, not the truncated one. Conversation continues
  fine in practice — the user redirected, the agent adapts on the
  next turn — but the legacy archive is a minor degree off from
  lived experience for those specific turns.
- One wasted LLM completion per barge-in. Small.

We can revisit abort propagation in v1.1 if telemetry shows barge-in
rates that make this material. The agent already supports disconnect
mid-stream (it commits whatever was streamed so far, see
`flashback/http/routes/stream.py`), so adding abort later is purely a
Node-side wiring change.

### 5.4 Session wrap

The user ends the call by pressing the existing **End** button in the
chat surface, which Node already wires to `POST /session/wrap`. No
voice-specific lifecycle work required.

Edge cases:

- **Tab close / network drop without pressing End.** Working memory
  expires on its existing Valkey TTL. Symmetric with text-mode tab
  close today — nothing voice-specific.
- **Idle while ConvAI session is open.** Out of scope for v1. If we
  add an idle policy later, it lives on the Node side (ConvAI ws
  close → call `/session/wrap`).

## 6. Edge cases

| Case                                                       | Handling                                                                                                                                                                                       |
| ---------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| User goes silent for a long time                           | ConvAI's idle handling on the audio side. Working memory expires on its existing TTL if no further turns come in. No voice-specific timeout in v1.                                              |
| User barges in mid-reply                                   | ConvAI mutes TTS locally. Node lets the in-flight `/turn/stream` complete naturally — no abort. Durable transcript shows the full agent reply; user heard only the prefix. Acceptable trade-off in v1 (see §5.3). |
| `/turn/stream` errors mid-stream                           | Upstream emits `error` SSE event. Node translates to an OpenAI-shape chunk with `finish_reason:"stop"` and a short fallback sentence (`"Sorry, can you say that again?"`). ConvAI speaks it.   |
| `/turn/stream` first-token latency > N seconds             | ConvAI may speak a configured "thinking" filler. Pure ConvAI config; no agent change. Worth measuring once live.                                                                               |
| Question chips (skip/suppress/defer)                       | Rendered on the chat surface during a voice session — the screen is still there. Agent speaks the seeded question normally; contributor can tap a chip or redirect aloud (which goes through intent classifier as `switch` / `clarify`).                                  |
| Coverage tap fires during voice                            | Tap card renders on screen via `meta.taps`. The voice-mode prompt override (§4.2 rule 6) makes the agent **speak** the tap question instead of going acknowledgment-only. Contributor either taps a chip or speaks the answer. No dead-air gap.                          |
| Starter-phase archetype-style questions                    | Already surfaced inline as chat-rendered cards (not a separate modal). Voice mode handles them via the same tap-pending override — agent speaks the question, chat shows the chips. Same mechanism will pick up steady-phase tap cards if/when we extend them there.    |
| Theme unlock flow (locked → unlocked)                      | Locked-theme unlock requires the unlock modal + `/session/start` cycle. **Deferred to v1.1.** Voice sessions can deepen already-unlocked themes (via `session_metadata.theme_id`); locked themes can still auto-unlock on `rich` via the Extraction Worker tail.        |
| Identity merge suggestion fires                            | Existing behavior: surfaced as out-of-band Node toast. Mode-agnostic. No change.                                                                                                               |
| User speaks a language other than English                  | ConvAI supports multilingual STT/TTS via config. Out of scope for this spec — v1 is English only.                                                                                              |

## 7. Observability

Add `mode` as a structured-log field on:

- Every orchestrator span (turn pipeline, session start)
- Response Generator entry points
- The stream routes

So we can split p50/p95 metrics by mode and watch for regressions.

## 8. Roll-out

1. Land the `mode` field + working memory persistence (this repo).
2. Land the voice prompt variant in the Response Generator (this repo).
3. Add `mode` to observability (this repo).
4. Node team builds mint endpoint + adapter + barge-in handling.
5. Behind a feature flag on Node, dogfood the voice flow on a single
   subject with the team's own legacies.
6. Measure: first-audio latency, interruption smoothness, prompt
   regressions in text mode (should be zero — voice prompt is gated).
7. GA when Node + agent metrics are clean.

## 9. Out of scope

- Voice cloning (subject's voice). Forbidden per CLAUDE.md §1.
- Locked-theme unlock initiated *from* a voice session — the unlock
  modal + `/session/start` cycle is deferred to v1.1. Already-unlocked
  themes deepen fine in voice; locked themes still auto-unlock on
  `rich` via the Extraction Worker tail.
- Voice-only commands like "skip this question" routed to the chip
  surface. In v1, the contributor either taps the chip on screen or
  redirects conversationally (which goes through the intent classifier
  as `switch` / `clarify`). Future work could plumb voice commands
  through.
- Steady-phase tap cards. Currently tap cards only fire in starter
  phase. The voice tap-pending override is written to handle them
  wherever they fire, so extending tap cards to steady phase will not
  require voice-mode changes.
- Multilingual.
- Recording the audio. We keep the transcript via the existing turn
  log; raw audio is not stored.

## 10. Why this fits the existing system

- **Boundaries:** Per CLAUDE.md §3, Node is the auth and external-API
  boundary. The ElevenLabs key, mint flow, OpenAI-shape adapter, and
  barge-in plumbing all live in Node. This repo only learns there's
  another mode of response phrasing.
- **Invariants:** No change to any of the 24 invariants. Graph writes,
  embeddings, supersession, retrieval gating, themes — all
  mode-agnostic.
- **Additive:** A `mode` field with a `text` default means every
  existing caller continues to work without change. The voice prompt
  fragment runs only when `mode == "voice"`.
- **Streaming foundation:** `/turn/stream` and `/session/start/stream`
  shipped in 4f21e75 specifically as prep for voice. Voice mode is the
  application of that work; the SSE contract is reused, not redesigned.

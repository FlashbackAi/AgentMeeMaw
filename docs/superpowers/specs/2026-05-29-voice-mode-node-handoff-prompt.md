# Node-side build prompt — Voice Mode (ElevenLabs ConvAI)

> **⚠️ SUPERSEDED (2026-06-02).** Voice mode moved off ElevenLabs ConvAI
> to decoupled Gemini STT + TTS. The Node-side contract is now in
> `2026-06-02-voice-mode-gemini-design.md` §4 — the OpenAI-shape
> `/chat/completions` adapter is gone, and Node owns VAD / turn-taking /
> barge-in directly (or via Pipecat / LiveKit). Kept for history.

Use this as the prompt for whoever (human or Claude Code) builds the
Node-side voice integration. The Python agent service is already wired
for voice mode; everything below is what's left.

---

## Context you need before writing code

You are working in the **Flashback Node backend repo** (separate from
the Python agent service). You're adding a real-time voice
conversation mode to Legacy Mode using ElevenLabs Conversational AI
for STT + TTS + VAD + turn-taking + barge-in, with the Python agent's
`/turn/stream` as the LLM brain.

**Mental model: voice = screen + voice, not audio-only.** The browser
still renders the existing chat UI during a voice session — the
transcript, question chips, tap cards, archetype modals all appear
on screen exactly like text mode. Audio is an additional input/output
channel layered on top. The contributor can tap a chip, fill the
archetype modal, *and* speak — and they hear the agent's voice while
seeing the chat scroll. This is Gemini Live's UX, not a phone call.

Practically that means the browser holds **two parallel channels**:

1. A WebSocket to ConvAI for audio I/O (mic in, TTS out).
2. The existing Node chat channel (SSE or WebSocket — whatever you use
   today in text mode) for the transcript and interactive elements.
   This channel is unchanged; you're just fanning the same agent
   stream into it from a different path.

Read these in order before touching code:

1. `docs/superpowers/specs/2026-05-29-voice-mode-elevenlabs-design.md`
   in the Python agent repo — the architecture document. The "Node-side
   responsibilities" section is your spec.
2. The agent's `NODE_INTEGRATION.md` — the existing contract you're
   extending. Voice mode follows the same auth + transport patterns.
3. The agent's `API.md` § `POST /turn/stream` and `POST /session/start`
   — the SSE shape you'll be consuming and translating.

Hard rules (from CLAUDE.md §3 in the agent repo):

- **You are the auth boundary.** The agent service has no auth; trust
  is "you + a service token + private network." The same applies to
  voice — the ElevenLabs API key never leaves Node, ConvAI's webhook
  is authenticated by you, and the browser only gets short-lived
  signed URLs.
- **You never write to the canonical graph.** Voice mode does not
  change this. Your role is mint signed URLs, run the LLM adapter, and
  proxy text in/out of `/turn/stream`. The agent owns all extraction,
  embeddings, threads, themes.
- **You never call ElevenLabs from the browser.** Always proxy.

## What the Python agent has already done

Already shipped on the agent side; you don't need to touch any of it:

- `POST /session/start`, `POST /session/start/stream`, `POST /turn`,
  `POST /turn/stream` all accept a new optional `mode` field on the
  request body: `"text" | "voice"` (default `"text"`). Pass `"voice"`
  when minting voice sessions; pass nothing for normal chat.
- The mode is persisted in the agent's Working Memory at session
  start so the response generator can swap to a voice-tailored prompt
  (no markdown, conversational rhythm, sparing ElevenLabs v3 audio
  tags like `[chuckles]`, `[softly]`, `[warm]`).
- **The tap-pending dead-air bug is fixed.** In text mode, when a
  coverage tap fires the agent replies with acknowledgment only
  ("Got it — happy to move on from that") because the chip card is
  the next question. In voice mode, that would be silent — the chip
  is on screen but nothing was spoken. The voice prompt now overrides
  that branch and makes the agent *speak* the tap question. The chip
  card still renders on screen; the user can tap or speak. Same fix
  applies if/when tap cards are extended to steady phase.
- `mode` is bound into the agent's structured logs on every voice
  request, so we can split p50/p95 metrics by mode after launch.
- Everything else (intent classifier, retrieval, segment detector,
  extraction worker, thread detector, themes, profile facts) is
  mode-agnostic and runs exactly as in text mode.

## What you build (the deliverables)

### 1. `POST /api/voice/session` — mint a ConvAI signed URL

The browser calls this when the user taps "Start voice session." You:

1. Validate the contributor's auth and authorize them against the
   `person_id`.
2. Call the agent's `POST /session/start` with `mode: "voice"`. Hold
   the returned `opener` string — you'll pass it to ConvAI in step 3.
   (If you prefer the streaming variant, call `/session/start/stream`
   and concatenate `text_delta` events into the opener. JSON is
   simpler and `/session/start` is not on the latency-critical path.)
3. Call ElevenLabs' "Get signed URL" endpoint to mint a ConvAI
   conversation. Pass these `dynamic_variables`:

   ```json
   {
     "session_id":   "<uuid from /session/start>",
     "person_id":    "<uuid>",
     "role_id":      "<uuid>",
     "mode":         "voice",
     "subject_name": "<person.display_name>"
   }
   ```

   Also set the agent's `first_message` (a.k.a. `agent_first_message`
   depending on the ConvAI SDK version) to the `opener` from step 2.
   That's the opening line ConvAI's TTS will speak before the user
   says anything.

4. Return `{ signed_url, session_id }` to the browser. The browser
   connects directly to ConvAI via that signed URL. The ElevenLabs
   API key never leaves Node.

### 2. `POST /voice/llm/chat/completions` — the LLM adapter

ConvAI's "Custom LLM" feature posts to this endpoint when it needs the
agent's next reply. ConvAI speaks the **OpenAI Chat Completions**
protocol — that's the *only* shape it knows how to send. Your
adapter's job is to translate.

Request shape from ConvAI:

```http
POST /voice/llm/chat/completions
Authorization: Bearer <secret you configured in ConvAI>
Content-Type: application/json

{
  "model": "<ignored — ConvAI lets you set this, we don't use it>",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."},
    {"role": "user", "content": "<latest transcript>"}
  ],
  "stream": true,
  "dynamic_variables": {
    "session_id": "...",
    "person_id":  "...",
    "role_id":    "...",
    "mode":       "voice",
    "subject_name": "..."
  }
}
```

Authenticate the request (shared secret in `Authorization` header that
you configured when you set up the ConvAI agent). Then:

1. Extract `session_id`, `person_id`, `role_id` from
   `dynamic_variables`. If any is missing or malformed → return 400.
2. Pull the **latest user message** off `messages[]` (the last entry
   with `"role": "user"`). You don't need the full history — the
   agent's working memory already has it. ConvAI sends the history
   because the OpenAI protocol requires it, but the agent reads its
   own transcript from Valkey on every turn.
3. Open an SSE connection to the agent:

   ```http
   POST {AGENT_BASE_URL}/turn/stream
   X-Service-Token: <your service token>
   Content-Type: application/json

   {
     "session_id":  "<from dynamic_variables>",
     "person_id":   "<from dynamic_variables>",
     "role_id":     "<from dynamic_variables>",
     "message":     "<latest user message>",
     "mode":        "voice"
   }
   ```

4. The agent will stream four kinds of SSE events. Translate **and
   fan out**: ConvAI gets the OpenAI SSE shape for TTS; the browser's
   chat channel gets the chips/taps and the same text (so the
   transcript renders while the audio plays).

   | Agent event   | → ConvAI                                                              | → Browser chat channel                                                              |
   | ------------- | --------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
   | `meta`        | Drop.                                                                  | Forward `taps`, `question_chips`, `intent` so the chat UI can render chip rows + tap cards exactly as in text mode. |
   | `text_delta`  | Re-emit as OpenAI SSE chunk (see below).                              | Forward as a chat-channel delta so the transcript renders in real time alongside the audio. |
   | `done`        | Emit terminal OpenAI chunk + `data: [DONE]\n\n`.                      | Forward `done` so the chat UI can finalize the assistant turn (timestamp, etc.).   |
   | `error`       | Emit fallback chunk `"Sorry, can you say that again?"` + terminal `done`. Log the error. | Forward as a chat-channel error event so the UI can surface a banner if needed.    |

   The fan-out matters: if chips and taps don't reach the chat
   channel, voice mode loses the affordances that text mode has. The
   agent's tap-pending behavior is already adjusted for voice (it
   *speaks* the question), so the contributor hears the question and
   sees the chip card on screen — they can tap or speak the answer.

5. OpenAI SSE chunk shape (per `text_delta`):

   ```
   data: {"id":"chatcmpl-<uuid>","object":"chat.completion.chunk","created":<unix>,"model":"flashback-voice","choices":[{"index":0,"delta":{"content":"<text chunk>"},"finish_reason":null}]}\n\n
   ```

   First chunk should also include `{"role":"assistant"}` in `delta`.
   Terminal chunk has `finish_reason:"stop"` and empty `delta`.

6. Flush the response stream aggressively (`res.flushHeaders()` /
   `res.flush()` after each chunk in Express; equivalent in your
   framework). ConvAI will not start TTS until it sees chunks
   arriving, and any buffering kills the latency win.

### 3. Barge-in (audio cutoff only — no server-side abort)

When the user starts speaking over the agent:

1. ConvAI mutes TTS playback locally — the user stops hearing the
   agent immediately. **You do nothing on the server.**
2. ConvAI closes the SSE connection to your webhook for that turn. Let
   the in-flight `/turn/stream` call complete naturally. Persist the
   full assistant reply to `legacy_turns_v1` as usual.

Explicit trade-off we made for v1 simplicity:

- The durable transcript will occasionally show the agent saying
  slightly more than the user actually heard (the muted tail).
  Acceptable.
- One LLM completion per barge-in finishes in the background. Cheap.

Do **not** wire `AbortController` / `req.on('close')` to cancel the
upstream call. We may revisit in v1.1 if telemetry shows the trade-off
is material; until then, simpler code wins. The agent already
supports mid-stream disconnect (commits partial text on its side), so
adding abort later is purely a Node-side wiring change.

### 4. Session lifecycle

- **Start:** `/api/voice/session` (you build this) → calls
  `/session/start` (agent) with `mode: "voice"`.
- **Turns:** ConvAI handles the audio loop; calls
  `/voice/llm/chat/completions` (you build this) for each LLM turn,
  which calls `/turn/stream` (agent) with `mode: "voice"`.
- **End:** User presses the existing **End** button in the chat
  surface, which already wires to `POST /session/wrap` in text mode.
  No voice-specific wrap path. Tab close or network drop without
  pressing End falls back to working-memory TTL expiry — same as text
  mode today.

### 5. Observability you should add on the Node side

- `mode` tag on every request log line so voice can be filtered
  separately.
- Histogram of latency from ConvAI POST received → first
  `text_delta` emitted upstream → first OpenAI chunk flushed
  downstream. This is the "time-to-first-audio" precursor — if it
  creeps up, voice feels laggy.
- (No barge-in counter for v1 — we don't propagate abort, so there's
  nothing for Node to count. If ConvAI exposes barge-in events on its
  ws side, log them from the browser instead; otherwise defer.)

## Constraints

- **Question chips and tap cards still render** during a voice
  session. Forward `meta` to the chat channel — do NOT drop them. The
  contributor either taps a chip / fills a card or speaks the answer;
  both work because intent classification handles natural redirection
  via `switch` / `clarify`.
- **Starter-phase archetype questions already render in the chat as
  cards** (not a separate modal). They follow the same `meta.taps`
  surface and are handled by the same code path. No special voice
  handling needed.
- **Locked-theme unlock initiated from voice is deferred to v1.1.**
  The unlock modal + `/session/start` cycle has a session-lifecycle
  awkwardness in the middle of a voice call. Voice sessions can
  *deepen* already-unlocked themes (pass `theme_id` in
  `session_metadata` on `/session/start`). Locked themes still
  auto-unlock on `rich` via the Extraction Worker tail.
- **Voice-command chip actions are out of scope for v1.** "Skip this
  question" spoken aloud goes through the intent classifier as a
  `switch` turn — it doesn't formally record a `skip` on the
  question_decisions table. The contributor can tap the chip on
  screen to do that explicitly.
- **No audio recording.** Transcripts are already persisted via the
  agent's normal turn log. Raw audio is not stored.
- **English only for v1.** ConvAI supports multilingual, but our
  prompts and tests aren't validated for other languages yet.

## Test plan

1. Wire the `/api/voice/session` and `/voice/llm/chat/completions`
   endpoints behind a feature flag.
2. Configure a single ElevenLabs ConvAI agent with:
   - Custom LLM webhook → your `/voice/llm/chat/completions`
   - First message → injected per session from the agent's opener
   - Voice → pick a warm, contemplative stock voice (no cloning per
     CLAUDE.md §1)
   - Audio tags → enable v3 model so `[chuckles]` etc. render
3. Open a voice session against your own legacy. Verify end-to-end:
   first audio arrives within a couple of seconds of you finishing
   speaking; mid-sentence interruption cuts the agent off cleanly;
   the transcript in the legacy review surface looks right.
4. Compare p50/p95 first-token latency (text mode) vs. first-audio
   latency (voice mode) — first-audio should be ~text first-token +
   ~few hundred ms for ConvAI TTS warmup. If it's seconds slower,
   something is buffering.
5. Confirm a text session started against the same person still
   works unchanged.

## What success looks like

A contributor opens the Flashback app, taps "Start voice session,"
and within a few seconds they're talking to the agent about their
mom. The agent's voice is warm and unhurried; when they cut in
mid-sentence, the agent stops cleanly and listens. After the
session, the legacy review surface shows the same moments, entities,
threads, and themes it would have shown if they'd typed instead.

When you're done, report back:

- The two endpoint URLs and the auth setup.
- One end-to-end latency measurement (first-audio p50).
- Any deviations from this spec and why.

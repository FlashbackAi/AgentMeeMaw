# Voice Mode — Gemini STT + TTS migration

**Status:** Implemented (agent side)
**Date:** 2026-06-02
**Supersedes:** `2026-05-29-voice-mode-elevenlabs-design.md` and
`2026-05-29-voice-mode-node-handoff-prompt.md`.

Migrates voice mode off **ElevenLabs Conversational AI** onto a
**decoupled Gemini STT → agent → Gemini TTS** pipeline. The motivation is
cost: ElevenLabs ConvAI bundles STT + LLM-orchestration + TTS at
~$0.11/user/minute. Decoupling lets us pay only for Gemini STT and TTS
tokens (much cheaper) while keeping our own agent as the brain.

Existing text-mode `/turn` and `/turn/stream` paths are **unchanged**.

---

## 1. What changed and why

ElevenLabs ConvAI had one feature we leaned on hard: **bring-your-own-LLM**.
ConvAI did ears + mouth + VAD + turn-taking + barge-in, but called *our*
`/turn/stream` as the LLM via an OpenAI-shape webhook — so the whole
Flashback agent (intent classifier, retrieval, response generator,
segment detector, extraction) kept running.

Gemini has no single drop-in equivalent:

- **Gemini Live API** is the true ConvAI-equivalent (native bidirectional
  audio, built-in VAD, interruption) — but the Gemini model *is* the
  brain. It can't slot our custom Python agent in as the LLM, so we'd
  lose our pipeline and break the core product. **Rejected.**
- **Decoupled STT + TTS** keeps our agent untouched and matches the
  cost goal. **Chosen.**

Consequence: ConvAI gave us VAD / turn-taking / barge-in for free.
Decoupled means **Node** now owns those (directly, or via a realtime
framework like Pipecat / LiveKit on the Node side). This is a Node-repo
change; per CLAUDE.md §3 Node is the external-API and orchestration
boundary.

## 2. Architecture

```
            ┌──────────── Node (separate repo) ────────────────┐
 Browser ──▶│ Gemini STT (streaming)  ─ transcript ─┐          │
  mic       │   + VAD / turn-taking / barge-in       │          │
            │                                        ▼          │
            │   POST /turn/stream {mode:"voice"}  ┌──────────┐  │
            │    ─────────────────────────────▶  │ THIS REPO│  │
            │    SSE: meta / voice_style /        │  agent   │  │
            │         text_delta / done       ◀── └──────────┘  │
            │                                        │          │
            │   accumulate reply + voice_style       ▼          │
            │   Gemini TTS (styled) ── audio ───────────────────┼─▶ Browser
            └───────────────────────────────────────────────────┘  speaker
```

- **Browser ↔ Node** carries audio *and* the existing chat surface
  (transcript, chips, tap cards, archetype modals). Voice = screen +
  voice, not audio-only.
- **Node ↔ this repo** is the existing `/turn/stream` SSE contract,
  called with `mode="voice"`. No protocol change.
- The OpenAI-shape `/chat/completions` adapter ConvAI required is
  **deleted** — that protocol only existed to satisfy ConvAI's webhook.
  Node now orchestrates our SSE directly.

This repo never speaks to Gemini's audio APIs and never speaks OpenAI
shape. It only learns there's a `voice` mode that changes reply phrasing
and emits a prosody label.

## 3. Changes in this repo (all additive)

The only Gemini-specific concept that reaches this repo is **prosody**.
ElevenLabs v3 parsed inline bracketed tags (`[softly]`, `[chuckles]`)
mid-text. Gemini TTS instead takes prosody as a **per-utterance style
instruction** set on the TTS call — not inline. So:

### 3.1 Style tag, lifted out of the reply

In voice mode the Response Generator prefixes its reply with **exactly
one** style tag — `[[style: <label>]]` — chosen by the same LLM that
writes the words (mirroring how ElevenLabs mode let the model pick
`[softly]`). The agent strips the tag before any text is shown or spoken
and surfaces the label to Node, which maps it to a Gemini TTS style.

Whitelist (collapsed from the old v3 tag set):

| label        | when                                          | old v3 tag(s)              |
|--------------|-----------------------------------------------|----------------------------|
| `warm`       | affectionate, fond framings                   | `[warm]`, `[chuckles]`     |
| `tender`     | grief, gentleness, weight — speak softly      | `[softly]`                 |
| `curious`    | leaning into a follow-up                      | `[curious]`                |
| `thoughtful` | a careful, considered beat before a question  | `[thoughtful]`, `[gentle pause]` |
| `wistful`    | bittersweet recollection, a quiet exhale      | `[sighs]`                  |
| `neutral`    | default when none fits                        | (no tag)                   |

A missing or unknown label falls back to `neutral`. The full mapping to
concrete Gemini voice/style strings lives on the Node side (it owns the
TTS call).

### 3.2 Where `voice_style` surfaces

- **Streaming** (`/turn/stream`, `/session/start/stream`): a new
  `voice_style` SSE event `{"style": "..."}` is emitted **once, before
  the first `text_delta`**, and is also included in the `done` event.
  Emitted only in voice mode.
- **JSON** (`/turn`, `/session/start`): `metadata.voice_style` on the
  response. `null` in text mode.

### 3.3 Code surface

- `flashback/response_generator/voice_style.py` — `extract_voice_style()`
  (non-streaming) and `VoiceStyleStreamParser` (streaming, strips a tag
  that may span chunks; withholds at most the first couple of tokens).
- `VOICE_MODE_INSTRUCTIONS` in `prompts.py` — rewritten: drop v3 inline
  tags, add the single leading-style-tag rule. All other voice rules
  (no markdown, spoken register, no UI narration, length, tap-pending
  override) are unchanged.
- `ResponseResult.voice_style`, `TurnState.voice_style`,
  `SessionStartState.voice_style`, `TurnResult.voice_style`,
  `SessionStartResult.voice_style`, `StreamEvent` `voice_style` type,
  `TurnMetadata.voice_style`, `SessionStartMetadata.voice_style`.

### 3.4 Things that explicitly do not change

`mode` request field, working-memory persistence of `mode`, the
`/turn/stream` SSE contract shape, the intent classifier, retrieval,
segment detector, extraction worker, thread detector, all 24 invariants.
The tap-pending override (agent *speaks* the tap question in voice mode)
is unchanged.

## 4. Node-side responsibilities (separate repo — informative)

This is the contract Node implements; not built here.

1. **Audio I/O + VAD + turn-taking + barge-in.** Formerly ConvAI's job.
   Build directly on Gemini's streaming STT, or adopt a realtime
   framework (Pipecat / LiveKit). Barge-in: cut TTS playback locally on
   the audio side. As today, Node need not abort the in-flight
   `/turn/stream` — the agent commits whatever streamed (see
   `stream.py`); revisit abort propagation only if barge-in telemetry
   makes it material.
2. **STT.** Stream mic audio to Gemini/Google streaming Speech-to-Text;
   on end-of-turn (VAD), take the final transcript as the user message.
3. **Brain.** `POST /turn/stream {session_id, person_id, role_id,
   message, mode:"voice"}` to this repo. Read the `voice_style` event +
   `text_delta` stream + `done`.
4. **TTS.** Accumulate the reply text (sentence-chunk if you want lower
   first-audio latency), call Gemini TTS with the style mapped from
   `voice_style`, stream audio back to the browser.
5. **Opener.** Use the JSON `/session/start` (`mode:"voice"`); read
   `opener` + `metadata.voice_style`, synthesize, speak.
6. **Chat surface.** Forward `meta` (chips, taps) to the browser's chat
   channel and render exactly as text mode. Drop the `voice_style` event
   from the chat channel (it's a TTS hint, not chat UI).
7. **Session wrap.** Unchanged — the existing End button → `/session/wrap`.

## 5. Out of scope

- Voice cloning (forbidden, CLAUDE.md §1).
- Gemini Live end-to-end (would replace our brain — rejected, §1).
- Locked-theme unlock initiated from a voice session (deferred, as before).
- Multilingual; audio recording (transcript only, via the turn log).

## 6. Verification

- `tests/response_generator/test_voice_style.py` covers tag extraction,
  case/space tolerance, cross-chunk splitting, no-tag passthrough,
  unknown-label fallback, and that the tag never leaks into emitted text.
- Full `tests/response_generator` suite green (40 passed).
- The 7 orchestrator HTTP integration tests that need a live Postgres
  pool are environment-gated and unrelated to this change.

# Step 7 - Response Generator + Starter Opener

This step replaces the canned turn reply with the first prose-producing
LLM component in the service. The Response Generator is a plain-text
big-model call, shaped by intent-specific prompts and fed with Working
Memory plus retrieval context.

## What It Ships

```
src/flashback/
    llm/interface.py              call_text(provider=..., ...)
    response_generator/
        schema.py                 TurnContext, StarterContext, ResponseResult
        prompts.py                five intent prompt families + starter opener
        context.py                compact XML-ish context rendering
        generator.py              ResponseGenerator
    orchestrator/
        orchestrator.py           intent -> retrieval -> generation
        protocol.py               HTTP-facing protocol and result shapes
        stub.py                   compatibility imports
    http/errors.py                LLMError -> HTTP 503
    config.py                     response-generator env knobs
```

## Prompt Families

The generator has one system prompt per intent:

- `clarify`: ask one gentle clarifying question.
- `recall`: use retrieved context to show continuity, then invite detail.
- `deepen`: acknowledge emotional weight without probing.
- `story`: reflect lightly and let the contributor continue.
- `switch`: offer 2-3 concrete directions, using retrieval if available.

The starter opener is a separate prompt family. It must name the person,
briefly identify Flashback, and ask the selected starter anchor. It also
forbids "how are you," loss/death language, and condolence platitudes in
the opener.

## Error Model

Intent classification and retrieval remain graceful. If classification
fails, the orchestrator logs it and still calls the Response Generator
with `story` as the safe default prompt, while returning `intent = null`
in metadata. If retrieval fails, the turn continues with empty retrieval
context.

Response generation is deliberately hard-fail. `LLMError`,
`LLMTimeout`, and `LLMMalformedResponse` propagate to the HTTP layer and
return:

```json
{
  "error": "service_unavailable",
  "detail": "response generation failed"
}
```

That is intentional: a canned fallback is worse than a clean failure in
a memorial conversation.

## Streaming

Streaming is deferred. `/session/start` and `/turn` still return JSON
bodies so Node's step-4 API contract remains unchanged. The generator is
structured around a single `call_text()` call, so a later streaming
adapter can be added without changing the orchestrator's context-building
surface.

## Starter Anchor Placeholder

Step 7 selects a random active `starter_anchor` from `active_questions`:

```sql
SELECT id, text, attributes->>'dimension'
FROM active_questions
WHERE source = 'starter_anchor'
ORDER BY random()
LIMIT 1;
```

Step 8 replaces this with Phase Gate selection based on starter
coverage. This placeholder gives `/session/start` a real generated
opener path now.

## Configuration

```
LLM_RESPONSE_PROVIDER=anthropic
LLM_RESPONSE_MODEL=claude-sonnet-4-6
LLM_RESPONSE_TIMEOUT_SECONDS=12
LLM_RESPONSE_MAX_TOKENS=400
```

`LLM_RESPONSE_MODEL` inherits `LLM_BIG_MODEL` when unset. No new
top-level dependencies were added.

## Verified

- [x] `call_text()` translates Anthropic and OpenAI plain-text calls
      without tool payloads.
- [x] Prompt drift tests cover all five classifier intents.
- [x] Context rendering omits empty retrieval sections.
- [x] Generator tests cover prompt selection, starter opener, stripping,
      and LLM error propagation.
- [x] Orchestrator integration tests cover generated `/session/start`,
      recall context in `/turn`, hard 503 on response generation failure,
      and classifier-failure fallback to the `story` prompt.
- [x] Full local suite:
      `python -m pytest` -> **124 passed, 41 skipped**.
- [ ] Live `uvicorn flashback.http.app:create_app --factory` smoke test
      against real Valkey/Postgres and real API keys.

## Deviations

- **Package layout:** implemented under `src/flashback/...`, matching
  the existing package layout, rather than the prompt shorthand
  `src/...`.
- **Working Memory bracketing:** the HTTP gateway still initializes WM
  and appends user/assistant turns, matching the step-4 route contract.
  The orchestrator now owns classification, retrieval, context assembly,
  and response generation inside that bracket.
- **`deepen` wording:** the architecture doc's one-line summary says
  `deepen` asks sensory questions, but the step-7 prompt and grief-tech
  rules say high-emotion turns should get presence without probing. The
  implemented prompt follows the step-7 prompt.
- **Compatibility module:** `orchestrator/stub.py` remains as a thin
  import shim so earlier tests and imports continue to work while the
  real class lives in `orchestrator/orchestrator.py`.

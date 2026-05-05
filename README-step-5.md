# Step 5 - Intent Classifier

This step adds the first live LLM seam in the agent service: the Intent
Classifier. It also introduces a provider-agnostic LLM layer that later
components can reuse for both small and big calls.

## What It Ships

```
src/flashback/
    llm/
        clients.py          OpenAI + Anthropic async client factories
        interface.py        call_with_tool(provider=..., ...)
        tool_spec.py        provider-neutral ToolSpec
        errors.py           LLMError / LLMTimeout / LLMMalformedResponse
    intent_classifier/
        classifier.py       IntentClassifier
        prompts.py          system prompt + classify_intent tool
        schema.py           IntentResult
    orchestrator/stub.py    classifies turns and writes WM signals
```

The file paths follow the repo's existing `flashback` package layout.

## LLM Layer

Small calls default to OpenAI `gpt-5.1`; big calls default to
Anthropic `claude-sonnet-4-6`.

Every component calls the same adapter:

```python
await call_with_tool(
    provider="openai",
    model="gpt-5.1",
    system_prompt=SYSTEM_PROMPT,
    user_message=user_block,
    tool=INTENT_TOOL,
    max_tokens=300,
    timeout=8,
    settings=settings,
)
```

The tool is defined once as a `ToolSpec`. `interface.py` translates it
to Anthropic Messages tool use or OpenAI Chat Completions function
calling, forcing the requested tool in both providers.

## Intents

- `clarify`: ambiguous reference; ask a follow-up before continuing.
- `recall`: user is asking to revisit something from earlier.
- `deepen`: emotionally heavy moment; give space rather than probing.
- `story`: user is narrating; respond lightly and let them continue.
- `switch`: user signals this topic is done and wants another direction.

The classifier also emits `confidence` and `emotional_temperature`.
Only `intent` and `emotional_temperature` are surfaced to Node and
written back into Working Memory.

## Graceful Degradation

If classification fails for any reason, the orchestrator logs the
failure, returns the unchanged stub reply, and leaves
`metadata.intent` / `metadata.emotional_temperature` as `null`. It does
not 5xx the user, and it does not write classifier output signals.

## Working Memory Signals

On success, the orchestrator writes:

```
signal_last_intent = result.intent
signal_emotional_temperature_estimate = result.emotional_temperature
```

Later steps consume those fields:

- Retrieval uses intent to decide when graph context is needed.
- Response Generator chooses prompt behavior by intent.
- Phase Gate can react to `switch` when that component lands.
- Segment Detector can use emotional temperature as pacing context.

## Running

```bash
pip install -e ".[dev]"
python -m pytest
```

To boot the service:

```bash
export DATABASE_URL=postgresql://flashback:flashback@localhost:5432/flashback
export VALKEY_URL=redis://localhost:6379/0
export SERVICE_TOKEN=changeme
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...

uvicorn flashback.http.app:create_app --factory
```

## Verified

- [x] LLM adapter unit tests cover Anthropic and OpenAI tool translation.
- [x] Intent prompt/schema drift tests cover enum and required-field sync.
- [x] Classifier tests cover success, LLM failures, windowing, and
      Pydantic validation.
- [x] `/turn` integration tests cover successful classifier metadata and
      graceful degradation.
- [ ] Live `uvicorn flashback.http.app:create_app --factory` smoke test
      against real Valkey/Postgres and real API keys.

## Deviations

- **Package layout:** implemented under `src/flashback/...`, matching
  the repo introduced in step 3, rather than prompt shorthand `src/...`.
- **OpenAI wire format:** used Chat Completions function calling with
  `tools`, forced `tool_choice`, and `max_completion_tokens`. OpenAI's
  current docs list Chat Completions as supported for GPT-5 models and
  document this tool-call surface, so the adapter keeps the fixed
  provider-neutral return contract while using that wire format.

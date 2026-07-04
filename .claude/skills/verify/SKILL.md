---
name: verify
description: Build/launch/drive recipe for verifying changes against the local Flashback dev stack (agent service + dev UI on :3001)
---

# Verifying against the local dev stack

## Prerequisites
1. Docker Desktop must be running (`docker info` to check; launch
   `C:\Program Files\Docker\Docker\Docker Desktop.exe` and wait ~30-60s).
2. Start the containers (they exist already, never `docker run`):
   `docker start flashback-postgres flashback-valkey flashback-localstack`
   - Postgres maps to :15432, Valkey :6379, localstack :4566.
3. Env comes from `.env.local` via `load_dotenv_local` — no manual export needed.

## Launch
```powershell
$env:PYTHONIOENCODING = "utf-8"   # dev.py banner has box-drawing chars; cp1252 console crashes without this
python local/dev.py                # single process, :3001, auto-opens browser
```
- `/api/*` → agent FastAPI app (in-process), everything else → dev UI/server.
- Readiness: `GET http://localhost:3001/health` (via /api? no — agent health is at `/api/health`; the dev UI polls it). The browser tab polling `/identity_merges/suggestions` in the log means it's up.

## Drive a conversation (the main surface)
1. `POST /create-person` body `{name, relationship, gender, contributor_gender}` → `{person_id, role_id}`.
2. `POST /api/session/start` body `{session_id: <new uuid>, person_id, role_id, session_metadata: {}}` → `{opener, metadata}`.
3. `POST /api/turn` body `{session_id, person_id, role_id, message}` → `{message, metadata: {intent, emotional_temperature, taps, ...}}`.
- Real LLM calls fire (OpenAI classifier, Anthropic responder) — each turn takes seconds and costs tokens.
- To trigger `deepen`: send an emotionally heavy line ("I was with him at the hospital at the end").
- To trigger a coverage tap: `switch`/`clarify`-shaped or terse messages; `metadata.taps` non-empty and the reply goes acknowledgment-only (no question) by design.

## Gotchas
- A person created via `/create-person` has no onboarding/moments and `/session/start`
  uses STARTER_OPENER_PROMPT (the first-time prompt only fires from the onboarding
  route). The prompt branches on the presence of `<prior_session_summary>`; to probe
  the returning-user path, pass `session_metadata.prior_session_summary` explicitly.
- State inspection: `GET /state?session_id=...` (Valkey WM + DB counts + queues),
  `GET /memories?person_id=...` (extraction inspector).

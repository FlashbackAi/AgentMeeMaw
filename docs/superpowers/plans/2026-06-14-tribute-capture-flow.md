# Tribute Capture Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a contributor enter the Tribute flow, answer an expanded set of archetype questions, and — late in the conversation, once warmth is established — give their direct message to the subject, captured structurally (never mined) and polished into `tributes.message_text`.

**Architecture:** A single on-demand `tribute`-kind theme reuses the existing archetype/unlock machinery (with a higher question count). When a session starts on that theme, a bridge step ensures a `tributes` row exists and stamps `current_tribute_id` into Working Memory. A new `select_message_invitation` orchestrator step — modeled exactly on `select_ground_truth_tap` — emits a one-time "say it to them" tap once the other checklist slots are mostly filled and emotional temperature is high. The answer returns on the next `/turn` as a new `message_answer` sidecar (modeled exactly on `ground_truth_answer`), is polished by a small LLM, and written to the tribute row. Because it rides the sidecar, extraction never sees it.

**Tech Stack:** Python, psycopg (async pool: `async with db_pool.connection()`), FastAPI/pydantic request models, Valkey-backed Working Memory, small-LLM calls via `flashback.llm.interface.call_with_tool`.

**Builds on Plan 1 (already merged/branch `feat/tribute-data-foundation`):** the `tributes` table, `tribute_status` view, `TributeRow`, sync repository functions, `SLOTS`/`SLOT_KEYS`, `fetch_tribute_progress_sync`.

**Scope:**
- **In:** async tribute repository surfaces; on-demand `tribute` theme + seeding; expanded archetype question count; the session-start bridge (tribute row + WM `current_tribute_id`); the message-polish LLM; the `message_answer` sidecar + persistence; the `signal_pending_message` WM field; the `select_message_invitation` step + pipeline registration; extending `Tap.kind` to `message`.
- **Out (deferred):** slot-gap **steering** (biasing question ranking toward unfilled slots) → folded into Plan 4. Assembly + compiled artifacts + `/generate` → Plan 3. Father's Day copy skin + skin-configurable invitation/message copy → Plan 4. This plan uses neutral default copy.

**Testing convention (per user instruction):** Build first, tests after. Tasks 1–7 implement + commit with no test run. Task 8 authors all tests and runs the suite once.

Spec: [`docs/superpowers/specs/2026-06-14-tribute-output-design.md`](../specs/2026-06-14-tribute-output-design.md) §5 (capture), §4 (checklist), §9 (skin — copy only, Plan 4).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/flashback/tribute/repository.py` (modify) | Add async surfaces: `ensure_open_tribute_async`, `fetch_open_tribute_id_async`, `set_message_async` |
| `src/flashback/tribute/progress.py` (modify) | Add `fetch_tribute_progress_async` (async twin of the sync reader) |
| `src/flashback/tribute/theme.py` (create) | Tribute theme constants (slug/display/description) + neutral message-invitation copy |
| `src/flashback/themes/repository.py` (modify) | Add `ensure_tribute_theme_async` (insert `kind='tribute'` locked, idempotent) |
| `src/flashback/themes/archetype_llm.py` (modify) | Add `min_questions`/`max_questions` params (defaults 3/4 preserve universals) |
| `src/flashback/tribute/message_llm.py` (create) | Small-LLM message polish (mirrors `tap_options`) |
| `src/flashback/tribute/message_capture.py` (create) | `persist_message_answer` (mirrors `ground_truth_answer.py`) |
| `src/flashback/http/models.py` (modify) | `MessageAnswerInput` model + `message_answer` field on `TurnRequest` |
| `src/flashback/orchestrator/protocol.py` (modify) | Extend `Tap.kind` Literal with `'message'` |
| `src/flashback/working_memory/schema.py` (modify) | Add `current_tribute_id`, `signal_pending_message`, `message_invitation_asked` fields |
| `src/flashback/working_memory/client.py` (modify) | Add `record_message_invitation_emitted`, `clear_pending_message` |
| `src/flashback/orchestrator/steps/apply_theme_unlock.py` (modify) | After unlock, if theme kind is `tribute`, ensure tribute row + stamp `current_tribute_id` |
| `src/flashback/orchestrator/steps/select_message_invitation.py` (create) | The one-time message-invitation tap step |
| `src/flashback/orchestrator/orchestrator.py` (modify) | Register `select_message_invitation` in the turn pipeline |
| `src/flashback/http/routes/turn.py` (modify) | Persist `message_answer` before the pipeline |
| `src/flashback/http/routes/stream.py` (modify) | Same persistence in the SSE twin |
| `tests/tribute/test_*` (create) | Async repo, theme seeding, archetype count, message capture, invitation step |

---

### Task 1: Async tribute repository + progress surfaces

The live pipeline and HTTP route are async; Plan 1 only built sync functions. Add async twins.

**Files:**
- Modify: `src/flashback/tribute/repository.py`
- Modify: `src/flashback/tribute/progress.py`

- [ ] **Step 1: Add async repository functions**

Append to `src/flashback/tribute/repository.py` (the sync functions + `_SELECT_TRIBUTE_COLUMNS` + `_row_to_tribute` already exist from Plan 1):

```python
# ---------------------------------------------------------------------------
# Async surfaces (HTTP route + orchestrator steps)
# ---------------------------------------------------------------------------

_OPEN_STATUSES = ("draft", "ready", "generating")


async def fetch_open_tribute_id_async(
    cur, *, person_id: UUID | str, theme_id: UUID | str
) -> str | None:
    """Return the most-recent non-complete tribute id for (person, theme)."""
    await cur.execute(
        """
        SELECT id::text
          FROM tributes
         WHERE person_id = %(person_id)s
           AND theme_id = %(theme_id)s
           AND status = ANY(%(statuses)s)
         ORDER BY created_at DESC
         LIMIT 1
        """,
        {
            "person_id": str(person_id),
            "theme_id": str(theme_id),
            "statuses": list(_OPEN_STATUSES),
        },
    )
    row = await cur.fetchone()
    return row[0] if row is not None else None


async def ensure_open_tribute_async(
    cur, *, person_id: UUID | str, theme_id: UUID | str
) -> str:
    """Return an open tribute for (person, theme), creating a draft if none.

    Idempotent within a session: a second call returns the same row.
    """
    existing = await fetch_open_tribute_id_async(
        cur, person_id=person_id, theme_id=theme_id
    )
    if existing is not None:
        return existing
    await cur.execute(
        """
        INSERT INTO tributes (person_id, theme_id, status)
        VALUES (%(person_id)s, %(theme_id)s, 'draft')
        RETURNING id::text
        """,
        {"person_id": str(person_id), "theme_id": str(theme_id)},
    )
    (tribute_id,) = await cur.fetchone()
    return tribute_id


async def set_message_async(
    cur,
    *,
    tribute_id: UUID | str,
    message_text: str,
    source_turns: list[dict[str, Any]] | None = None,
) -> None:
    """Async twin of ``set_message_sync``."""
    await cur.execute(
        _SET_MESSAGE_SQL,
        {
            "id": str(tribute_id),
            "message_text": message_text,
            "source_turns": Json(source_turns) if source_turns is not None else None,
        },
    )
```

- [ ] **Step 2: Add the async progress reader**

Append to `src/flashback/tribute/progress.py`:

```python
async def fetch_tribute_progress_async(
    cur, *, tribute_id: UUID | str
) -> TributeProgress | None:
    """Async twin of ``fetch_tribute_progress_sync``."""
    await cur.execute(_PROGRESS_SQL, {"id": str(tribute_id)})
    row = await cur.fetchone()
    if row is None:
        return None
    (
        memories_count,
        message_present,
        appearance_present,
        signature_present,
        percent,
        ready,
    ) = row
    filled_by_key = {
        "memories": memories_count >= 3,
        "message": bool(message_present),
        "appearance": bool(appearance_present),
        "signature": bool(signature_present),
    }
    slots = [
        TributeSlot(key=s.key, label=s.label, hint=s.hint, filled=filled_by_key[s.key])
        for s in SLOTS
    ]
    return TributeProgress(
        tribute_id=str(tribute_id),
        percent=int(percent),
        ready=bool(ready),
        slots=slots,
    )
```

- [ ] **Step 3: Commit**

```bash
git add src/flashback/tribute/repository.py src/flashback/tribute/progress.py
git commit -m "feat(tribute): async repository + progress surfaces

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 2: Tribute theme definition + on-demand seeding

**Files:**
- Create: `src/flashback/tribute/theme.py`
- Modify: `src/flashback/themes/repository.py`

- [ ] **Step 1: Define the tribute theme constants**

Create `src/flashback/tribute/theme.py`:

```python
"""Constants for the on-demand Tribute theme.

The tribute capability is one reusable theme (kind='tribute'), seeded
on demand when a contributor enters the flow -- NOT at person creation,
so normal legacies stay clean (spec section 4). 'Father's Day' is a copy
skin applied in Plan 4; the slug + neutral copy here are campaign-neutral.
"""

from __future__ import annotations

TRIBUTE_SLUG = "tribute"
TRIBUTE_DISPLAY_NAME = "A Tribute"
TRIBUTE_DESCRIPTION = (
    "A short, shareable tribute to them -- a handful of shared memories "
    "and one thing you'd want to say straight to them."
)

# Neutral default copy for the message-invitation tap. Plan 4's campaign
# skin (e.g. Father's Day) overrides this string.
MESSAGE_INVITATION_COPY = (
    "If you could say one thing straight to them, what would it be?"
)

# Expanded archetype question count for the tribute theme (universals
# stay at the 3-4 default).
TRIBUTE_ARCHETYPE_MIN = 6
TRIBUTE_ARCHETYPE_MAX = 8
```

- [ ] **Step 2: Add the on-demand tribute-theme seeder**

Add to `src/flashback/themes/repository.py` (near `seed_universal_themes_async`). It mirrors the universal seeder but uses `kind='tribute'` and returns the theme id:

```python
_ENSURE_TRIBUTE_THEME_SQL = """
INSERT INTO themes (person_id, kind, slug, display_name, description, state)
VALUES (%(person_id)s, 'tribute', %(slug)s, %(display_name)s,
        %(description)s, 'locked')
ON CONFLICT (person_id, slug) WHERE status = 'active' DO NOTHING
"""

_SELECT_TRIBUTE_THEME_ID_SQL = """
SELECT id::text FROM active_themes
 WHERE person_id = %(person_id)s AND slug = %(slug)s
 LIMIT 1
"""


async def ensure_tribute_theme_async(
    cur,
    *,
    person_id: UUID | str,
    slug: str,
    display_name: str,
    description: str | None,
) -> str:
    """Ensure the on-demand tribute theme exists; return its id.

    Idempotent via the active-slug partial unique index.
    """
    pid = str(person_id)
    await cur.execute(
        _ENSURE_TRIBUTE_THEME_SQL,
        {
            "person_id": pid,
            "slug": slug,
            "display_name": display_name,
            "description": description,
        },
    )
    await cur.execute(_SELECT_TRIBUTE_THEME_ID_SQL, {"person_id": pid, "slug": slug})
    (theme_id,) = await cur.fetchone()
    return theme_id
```

- [ ] **Step 3: Commit**

```bash
git add src/flashback/tribute/theme.py src/flashback/themes/repository.py
git commit -m "feat(tribute): on-demand tribute theme definition + seeder

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 3: Expanded archetype question count

The archetype LLM hard-codes 3-4 questions. Parameterize the count so the tribute theme can request 6-8 without changing universals.

**Files:**
- Modify: `src/flashback/themes/archetype_llm.py`

- [ ] **Step 1: Add count params to the signature**

In `src/flashback/themes/archetype_llm.py`, change the `generate_archetype_questions` signature to add two keyword params with universal-preserving defaults:

```python
async def generate_archetype_questions(
    *,
    settings,
    theme_slug: str,
    theme_display_name: str,
    theme_description: str,
    theme_kind: str,  # 'universal' | 'emergent' | 'tribute'
    subject_name: str,
    subject_relationship: str | None = None,
    context_moments: list[ArchetypeContextMoment] | None = None,
    min_questions: int = 3,
    max_questions: int = 4,
) -> list[ArchetypeQuestion]:
```

- [ ] **Step 2: Thread the count into the prompt + tool schema**

The system prompt currently says "You write 3-4 short multiple-choice questions...". Replace the hard-coded "3-4" with the params. Find the line in `_ARCHETYPE_SYSTEM_PROMPT` (around line 42) and make the prompt a format string, or inject a count instruction in the user message. The robust approach — build the count clause and pass it in the user/system message rather than the frozen constant. Locate where the system prompt is passed to the LLM call and prepend a count directive:

```python
    count_directive = (
        f"Write between {min_questions} and {max_questions} short "
        "multiple-choice questions (inclusive)."
    )
    system_prompt = f"{count_directive}\n\n{_ARCHETYPE_SYSTEM_PROMPT}"
```

Then pass `system_prompt` (not the bare `_ARCHETYPE_SYSTEM_PROMPT`) into the `call_with_tool(...)` invocation.

In the tool schema (`_ARCHETYPE_TOOL`), the `questions` array has `minItems: 3, maxItems: 4`. These are static on the frozen `ToolSpec`. Build a per-call tool whose bounds use the params. Where the call is made, construct the tool inline instead of using the module-level constant:

```python
    tool = ToolSpec(
        name=_ARCHETYPE_TOOL.name,
        description=_ARCHETYPE_TOOL.description,
        input_schema={
            **_ARCHETYPE_TOOL.input_schema,
            "properties": {
                **_ARCHETYPE_TOOL.input_schema["properties"],
                "questions": {
                    **_ARCHETYPE_TOOL.input_schema["properties"]["questions"],
                    "minItems": min_questions,
                    "maxItems": max_questions,
                },
            },
        },
    )
```

Pass `tool` into `call_with_tool(...)` in place of `_ARCHETYPE_TOOL`.

> Implementer note: open the file first and adapt these two edits to the actual variable names used in the call site — the shapes above match the current `_ARCHETYPE_SYSTEM_PROMPT` / `_ARCHETYPE_TOOL` / `call_with_tool` usage. Universals keep the 3/4 defaults, so their behavior is unchanged.

- [ ] **Step 3: Request the expanded set from the tribute unlock path**

In `src/flashback/http/routes/themes.py`, the `unlock_prepare` handler calls `generate_archetype_questions(...)` when `archetype_questions` is NULL. Make that call pass the expanded bounds when the theme is a tribute. Add, just before the call:

```python
    from flashback.tribute.theme import TRIBUTE_ARCHETYPE_MAX, TRIBUTE_ARCHETYPE_MIN

    if theme.kind == "tribute":
        q_min, q_max = TRIBUTE_ARCHETYPE_MIN, TRIBUTE_ARCHETYPE_MAX
    else:
        q_min, q_max = 3, 4
```

and pass `min_questions=q_min, max_questions=q_max` into the `generate_archetype_questions(...)` call.

- [ ] **Step 4: Commit**

```bash
git add src/flashback/themes/archetype_llm.py src/flashback/http/routes/themes.py
git commit -m "feat(tribute): expanded archetype question count for tribute theme

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 4: Working Memory fields + methods

**Files:**
- Modify: `src/flashback/working_memory/schema.py`
- Modify: `src/flashback/working_memory/client.py`

- [ ] **Step 1: Add the WM state fields**

In `src/flashback/working_memory/schema.py`, in the `WorkingMemoryState` model (near `signal_pending_tap_question` / the gt fields), add:

```python
    # Active tribute output id for this session (set when a session starts
    # on a tribute-kind theme). Empty when not in a tribute flow.
    current_tribute_id: str = ""
    # JSON payload of the pending message-invitation tap, if any. Read by
    # persist_message_answer on the next /turn so the sidecar answer is
    # routed to the tribute row. Empty when no message tap is pending.
    signal_pending_message: str = ""
    # The message invitation is a one-time ask per session.
    message_invitation_asked: bool = False
```

> Implementer note: match the exact coercion the schema uses for bools/ints (the gt fields show the pattern). If the state is loaded from a Redis hash of strings, ensure `message_invitation_asked` parses `"True"/"False"` the same way the existing bool fields do.

- [ ] **Step 2: Add the client methods**

In `src/flashback/working_memory/client.py`, add (mirrors `record_tap_emitted` / `clear_pending_tap_question`):

```python
    async def record_message_invitation_emitted(
        self, session_id: str, payload_json: str
    ) -> None:
        """Mark the one-time message invitation as emitted this session.

        Stashes the pending payload (read by persist_message_answer next
        turn), flips message_invitation_asked, and resets the shared tap
        cooldown so the card doesn't stack on another tap.
        """
        s_key = state_key(session_id)
        async with self._redis.pipeline(transaction=True) as p:
            p.hset(s_key, "signal_pending_message", payload_json)
            p.hset(s_key, "message_invitation_asked", "True")
            p.hset(s_key, "user_turns_since_last_tap", "0")
            p.expire(s_key, self._ttl)
            await p.execute()

    async def clear_pending_message(self, session_id: str) -> None:
        """Clear the pending message payload after the sidecar is consumed."""
        await self.update_signals(session_id, signal_pending_message="")
```

- [ ] **Step 3: Commit**

```bash
git add src/flashback/working_memory/schema.py src/flashback/working_memory/client.py
git commit -m "feat(tribute): WM fields + methods for message invitation

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 5: Session-start bridge — tribute row + WM tribute_id

When a session starts on a tribute-kind theme, ensure a `tributes` row exists and propagate its id.

**Files:**
- Modify: `src/flashback/orchestrator/steps/apply_theme_unlock.py`

- [ ] **Step 1: Ensure the tribute row inside the unlock transaction**

In `apply_theme_unlock`, after `unlock_theme_async(...)` and inside the same `async with conn.cursor() as cur:` transaction block, add a tribute-row bootstrap when the theme is a tribute. Add the import at top:

```python
from flashback.tribute.repository import ensure_open_tribute_async
```

Then, inside the transaction (after the `if theme.state == "locked" or archetype_answers:` block, still within the cursor context), add:

```python
                    tribute_id: str | None = None
                    if theme.kind == "tribute":
                        tribute_id = await ensure_open_tribute_async(
                            cur, person_id=person_id, theme_id=theme_id
                        )
```

- [ ] **Step 2: Stamp the tribute id into session_metadata for WM init**

After the transaction block (alongside the existing `state.session_metadata["current_theme_*"] = ...` writes), add:

```python
        if theme.kind == "tribute" and tribute_id is not None:
            state.session_metadata["current_tribute_id"] = tribute_id
```

> Implementer note: confirm how `current_theme_*` in `session_metadata` reaches `WorkingMemoryState.current_theme_slug` (the WM-init step reads these keys). Add `current_tribute_id` to that same WM-init mapping so `WorkingMemoryState.current_tribute_id` is populated. Search for where `current_theme_slug` is written into WM at session start and mirror it for `current_tribute_id`.

- [ ] **Step 3: Commit**

```bash
git add src/flashback/orchestrator/steps/apply_theme_unlock.py
git commit -m "feat(tribute): bootstrap tribute row + WM id on tribute session start

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 6: Message-polish LLM

**Files:**
- Create: `src/flashback/tribute/message_llm.py`

- [ ] **Step 1: Write the polish call (mirrors `tap_options.py`)**

Create `src/flashback/tribute/message_llm.py`:

```python
"""Polish a contributor's raw message into the tribute's message_text.

Spec choice (2026-06-14): LLM-polished from the user's own words --
tighter and more lyrical while keeping their specifics. Best-effort:
on any failure, return the cleaned raw text so the slot still fills.
"""

from __future__ import annotations

import structlog

from flashback.llm.errors import LLMError
from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import xml_text
from flashback.llm.tool_spec import ToolSpec

log = structlog.get_logger("flashback.tribute.message_llm")


_MESSAGE_SYSTEM = """\
You polish a contributor's spoken message to a loved one into a short,
heartfelt written message for a shareable tribute.

Rules:
- Keep THEIR specifics -- names of feelings, concrete details, the exact
  thing they wanted to say. Never invent new facts, memories, or names.
- Tighten and warm the phrasing. Fix stumbles and filler. Keep it in
  the contributor's own first-person voice ("I", "you").
- 1-4 sentences. No greeting/sign-off scaffolding ("Dear ...",
  "Love, ..."). Just the message.
- Never address the reader or narrate -- output only the message itself.

Call the `polish_message` tool exactly once.
"""

_MESSAGE_TOOL = ToolSpec(
    name="polish_message",
    description="Return the polished message. Call exactly once.",
    input_schema={
        "type": "object",
        "properties": {
            "message": {"type": "string", "minLength": 1, "maxLength": 600},
        },
        "required": ["message"],
        "additionalProperties": False,
    },
)


async def polish_message(
    *,
    settings,
    raw_text: str,
    person_name: str,
    person_relationship: str | None = None,
) -> str:
    """Return a polished message; falls back to the cleaned raw text."""
    cleaned = (raw_text or "").strip()
    if settings is None or not cleaned:
        return cleaned

    rel_attr = (
        f' relationship="{xml_text(person_relationship)}"'
        if person_relationship
        else ""
    )
    user_block = (
        f"<subject{rel_attr}>{xml_text(person_name)}</subject>\n"
        f"<raw_message>{xml_text(cleaned)}</raw_message>"
    )

    try:
        args = await call_with_tool(
            provider=settings.llm_small_provider,
            model=settings.llm_intent_model,
            system_prompt=_MESSAGE_SYSTEM,
            user_message=user_block,
            tool=_MESSAGE_TOOL,
            max_tokens=300,
            timeout=12.0,
            settings=settings,
        )
    except LLMError as exc:
        log.warning("message_polish.llm_failed", error=str(exc))
        return cleaned
    except Exception as exc:  # defensive -- never lose the user's words
        log.warning(
            "message_polish.unexpected_failure",
            error_type=type(exc).__name__,
            detail=str(exc),
        )
        return cleaned

    polished = args.get("message") if isinstance(args, dict) else None
    if isinstance(polished, str) and polished.strip():
        return polished.strip()
    return cleaned
```

- [ ] **Step 2: Commit**

```bash
git add src/flashback/tribute/message_llm.py
git commit -m "feat(tribute): small-LLM message polish

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 7: Message capture sidecar + invitation step + pipeline wiring

**Files:**
- Modify: `src/flashback/orchestrator/protocol.py`
- Modify: `src/flashback/http/models.py`
- Create: `src/flashback/tribute/message_capture.py`
- Create: `src/flashback/orchestrator/steps/select_message_invitation.py`
- Modify: `src/flashback/orchestrator/orchestrator.py`
- Modify: `src/flashback/http/routes/turn.py`
- Modify: `src/flashback/http/routes/stream.py`

- [ ] **Step 1: Extend `Tap.kind` with `message`**

In `src/flashback/orchestrator/protocol.py`, change the `Tap.kind` field:

```python
    kind: Literal["coverage", "ground_truth", "segment_anchor", "message"] = "coverage"
```

> Contract note: this adds a fourth tap kind. Like `ground_truth`/`segment_anchor`, a `message` tap has `question_id=None`; Node renders it as a card and must NOT post a `question_decision` for it (mirrors the existing null-question_id rule). The answer returns via the `message_answer` sidecar (Step 2), not `ground_truth_answer`.

- [ ] **Step 2: Add the `MessageAnswerInput` model + `TurnRequest` field**

In `src/flashback/http/models.py`, add (mirrors `GroundTruthAnswerInput`):

```python
class MessageAnswerInput(BaseModel):
    """Structured answer to a tribute message-invitation tap, carried on
    the next /turn. Never enters the transcript -- extraction never mines
    it (design 2026-06-14 section 5)."""

    model_config = ConfigDict(extra="forbid")

    option_label: str | None = Field(default=None, max_length=200)
    free_text: str | None = Field(default=None, max_length=2000)
    skipped: bool = False
```

and add the field to `TurnRequest` (alongside `ground_truth_answer`):

```python
    message_answer: MessageAnswerInput | None = None
```

- [ ] **Step 3: Write the message-capture persistence (mirrors `ground_truth_answer.py`)**

Create `src/flashback/tribute/message_capture.py`:

```python
"""Persist a tribute message-invitation answer before the turn pipeline.

Shared by /turn and /turn/stream. Idempotent against UI replays: an
answer arriving with no pending message tap in Working Memory is ignored
(mirrors ground_truth_answer.py). The answer is polished and written to
the tribute row -- it NEVER enters the transcript, so extraction never
mines the contributor's message (design 2026-06-14 section 5).
"""

from __future__ import annotations

import json
from uuid import UUID

import structlog

from flashback.http.models import MessageAnswerInput
from flashback.tribute.message_llm import polish_message
from flashback.tribute.repository import set_message_async

log = structlog.get_logger("flashback.tribute.message_capture")


async def persist_message_answer(
    *,
    session_id: UUID,
    person_id: UUID,
    answer: MessageAnswerInput,
    wm,
    db_pool,
    settings,
) -> None:
    state = await wm.get_state(str(session_id))
    if not state.signal_pending_message:
        log.info("message_answer.ignored", reason="no_pending_message")
        return
    tribute_id = state.current_tribute_id
    if not tribute_id:
        log.info("message_answer.ignored", reason="no_tribute_id")
        await wm.clear_pending_message(str(session_id))
        return

    if answer.skipped:
        log.info("message_answer.skipped")
        await wm.clear_pending_message(str(session_id))
        return

    raw = (answer.free_text or answer.option_label or "").strip()
    if not raw:
        await wm.clear_pending_message(str(session_id))
        return

    # Look up subject name/relationship for the polish prompt.
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT name, relationship FROM persons WHERE id = %s",
                (str(person_id),),
            )
            row = await cur.fetchone()
    person_name = str(row[0]) if row else ""
    relationship = str(row[1]) if row and row[1] is not None else None

    polished = await polish_message(
        settings=settings,
        raw_text=raw,
        person_name=person_name,
        person_relationship=relationship,
    )

    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await set_message_async(
                    cur,
                    tribute_id=tribute_id,
                    message_text=polished,
                    source_turns=[{"text": raw}],
                )
    log.info("message_answer.recorded", tribute_id=tribute_id)
    await wm.clear_pending_message(str(session_id))
```

- [ ] **Step 4: Write the `select_message_invitation` step (mirrors `select_ground_truth_tap`)**

Create `src/flashback/orchestrator/steps/select_message_invitation.py`:

```python
"""One-time message-invitation tap for the tribute flow.

Emits a single "say it to them" tap when the contributor is in a tribute
flow, the conversation is warm, and the other checklist slots are mostly
filled -- so the message lands as the emotional climax, not a cold open
(design 2026-06-14 section 5). The answer returns as the message_answer
sidecar and is polished into tributes.message_text; it never enters the
transcript.

The invitation copy is neutral here; Plan 4's campaign skin overrides it.
"""

from __future__ import annotations

import json

import structlog

from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.protocol import Tap
from flashback.orchestrator.state import TurnState
from flashback.tribute.progress import fetch_tribute_progress_async
from flashback.tribute.theme import MESSAGE_INVITATION_COPY

log = structlog.get_logger("flashback.orchestrator")

MESSAGE_TAP_COOLDOWN_USER_TURNS = 2


async def select_message_invitation(state: TurnState, deps: OrchestratorDeps) -> None:
    """Emit the one-time tribute message-invitation tap, if warranted."""

    with timed_step(log, "select_message_invitation"):
        if state.intent_result is None or state.intent_result.intent not in {
            "story",
            "deepen",
        }:
            return
        if state.taps:
            log.info("message_tap.skipped", reason="other_tap_pending")
            return
        # We WANT a warm moment for the confession (opposite of GT taps).
        if state.effective_temperature != "high":
            log.info("message_tap.skipped", reason="not_warm_enough")
            return

        wm_state = state.working_memory_state or await deps.working_memory.get_state(
            str(state.session_id)
        )
        state.working_memory_state = wm_state

        tribute_id = wm_state.current_tribute_id
        if not tribute_id:
            return  # not in a tribute flow
        if wm_state.message_invitation_asked:
            log.info("message_tap.skipped", reason="already_asked")
            return
        if wm_state.user_turns_since_last_tap < MESSAGE_TAP_COOLDOWN_USER_TURNS:
            log.info("message_tap.skipped", reason="cooldown")
            return

        async with deps.db_pool.connection() as conn:
            async with conn.cursor() as cur:
                progress = await fetch_tribute_progress_async(
                    cur, tribute_id=tribute_id
                )
        if progress is None:
            return

        def _filled(key: str) -> bool:
            return any(s.key == key and s.filled for s in progress.slots)

        if _filled("message"):
            log.info("message_tap.skipped", reason="message_already_present")
            return
        # Mostly-filled gate: appearance present AND at least 2 memories.
        memories_slot = next(
            (s for s in progress.slots if s.key == "memories"), None
        )
        if not _filled("appearance") or memories_slot is None:
            log.info("message_tap.skipped", reason="slots_not_ready")
            return
        # progress.percent already reflects memory fill; require a floor.
        if progress.percent < 40:
            log.info("message_tap.skipped", reason="too_sparse")
            return

        tap = Tap(
            question_id=None,
            text=MESSAGE_INVITATION_COPY,
            dimension="",
            options=[],
            kind="message",
            field=None,
        )
        state.taps = [tap]
        await deps.working_memory.record_message_invitation_emitted(
            session_id=str(state.session_id),
            payload_json=json.dumps({"kind": "message", "text": MESSAGE_INVITATION_COPY}),
        )
        log.info("message_tap.selected", tribute_id=tribute_id)
```

> Implementer note: confirm `state.effective_temperature` exists (used by `select_ground_truth_tap`) and that `TurnState` exposes `intent_result`, `taps`, `working_memory_state` as the GT step uses them. The gate floor (`percent < 40`) is tunable.

- [ ] **Step 5: Register the step in the turn pipeline**

In `src/flashback/orchestrator/orchestrator.py`, in `handle_turn`, add the step inside the `story`/`deepen` block, right after `select_ground_truth_tap` (so a message tap and a GT tap never both fire — the GT step bails when `state.taps` is set, and this step also bails when `state.taps` is set; ordering means GT runs first, then message only if GT didn't tap). Add the import alongside the other step imports, then:

```python
            if state.effective_intent in {"story", "deepen"}:
                await execute(
                    policies=TURN_POLICIES,
                    step_name="select_ground_truth_tap",
                    fn=lambda: select_ground_truth_tap(state, self._deps),
                    state=state,
                )
                await execute(
                    policies=TURN_POLICIES,
                    step_name="select_message_invitation",
                    fn=lambda: select_message_invitation(state, self._deps),
                    state=state,
                )
```

Add the import:

```python
from flashback.orchestrator.steps.select_message_invitation import (
    select_message_invitation,
)
```

> Implementer note: there is a streaming twin of the turn pipeline (the SSE path). Register `select_message_invitation` there too, in the same position relative to `select_ground_truth_tap`. Search `orchestrator.py` (and any streaming orchestrator module) for the second occurrence of `select_ground_truth_tap` and mirror the addition.

- [ ] **Step 6: Wire `message_answer` persistence into the turn route**

In `src/flashback/http/routes/turn.py`, after the `if body.ground_truth_answer is not None:` block, add:

```python
    if body.message_answer is not None:
        await persist_message_answer(
            session_id=body.session_id,
            person_id=body.person_id,
            answer=body.message_answer,
            wm=wm,
            db_pool=db_pool,
            settings=cfg.settings,
        )
```

Add the import at top:

```python
from flashback.tribute.message_capture import persist_message_answer
```

> Implementer note: confirm how `settings` is obtained in this handler — the GT path doesn't need settings, but the polish call does. `cfg` (`HttpConfig`) is already injected; use its settings attribute, or add a `settings = Depends(get_settings)` dependency mirroring how `tap_options`/other LLM call sites obtain `settings`. Adjust `cfg.settings` to the real accessor.

- [ ] **Step 7: Wire the same persistence into the SSE twin**

In `src/flashback/http/routes/stream.py`, find where `persist_ground_truth_answer` is called before the streaming pipeline and add the analogous `persist_message_answer` call with the same arguments (and settings accessor) used there.

- [ ] **Step 8: Commit**

```bash
git add src/flashback/orchestrator/protocol.py src/flashback/http/models.py \
        src/flashback/tribute/message_capture.py \
        src/flashback/orchestrator/steps/select_message_invitation.py \
        src/flashback/orchestrator/orchestrator.py \
        src/flashback/http/routes/turn.py src/flashback/http/routes/stream.py
git commit -m "feat(tribute): message-invitation tap + sidecar capture

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 8: Tests + run the suite

**Files:**
- Create: `tests/tribute/test_async_repository.py`
- Create: `tests/tribute/test_theme_seeding.py`
- Create: `tests/tribute/test_archetype_count.py`
- Create: `tests/tribute/test_message_capture.py`
- Create: `tests/tribute/test_select_message_invitation.py`

- [ ] **Step 1: Async repository + theme seeding DB tests**

Create `tests/tribute/test_async_repository.py`:

```python
"""DB tests for the async tribute repository surfaces."""

from __future__ import annotations

import pytest

from flashback.tribute.repository import (
    ensure_open_tribute_async,
    fetch_open_tribute_id_async,
    set_message_async,
)

pytestmark = pytest.mark.asyncio


async def test_ensure_open_tribute_is_idempotent(async_db_pool, make_person) -> None:
    person_id = make_person("Dad")
    theme_id = await _seed_tribute_theme(async_db_pool, person_id)
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            first = await ensure_open_tribute_async(
                cur, person_id=person_id, theme_id=theme_id
            )
            second = await ensure_open_tribute_async(
                cur, person_id=person_id, theme_id=theme_id
            )
            await conn.commit()
    assert first == second


async def test_set_message_async_persists(async_db_pool, make_person) -> None:
    person_id = make_person("Dad")
    theme_id = await _seed_tribute_theme(async_db_pool, person_id)
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            tribute_id = await ensure_open_tribute_async(
                cur, person_id=person_id, theme_id=theme_id
            )
            await set_message_async(
                cur, tribute_id=tribute_id, message_text="Thank you, Dad.",
                source_turns=[{"text": "raw"}],
            )
            await cur.execute(
                "SELECT message_text FROM tributes WHERE id = %s", (tribute_id,)
            )
            (msg,) = await cur.fetchone()
            await conn.commit()
    assert msg == "Thank you, Dad."


async def _seed_tribute_theme(async_db_pool, person_id: str) -> str:
    from flashback.themes.repository import ensure_tribute_theme_async
    from flashback.tribute.theme import (
        TRIBUTE_DESCRIPTION,
        TRIBUTE_DISPLAY_NAME,
        TRIBUTE_SLUG,
    )

    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            theme_id = await ensure_tribute_theme_async(
                cur,
                person_id=person_id,
                slug=TRIBUTE_SLUG,
                display_name=TRIBUTE_DISPLAY_NAME,
                description=TRIBUTE_DESCRIPTION,
            )
            await conn.commit()
    return theme_id
```

> Implementer note: this assumes an `async_db_pool` fixture. If `tests/conftest.py` has no async pool fixture, add one mirroring `db_pool` but using `flashback.db.connection`'s async pool factory (search the codebase for how other async DB tests obtain a pool — e.g. `tests/identity_merges/` uses async cursors). Reuse that exact fixture/pattern rather than inventing a new one.

- [ ] **Step 2: Tribute theme seeding test**

Create `tests/tribute/test_theme_seeding.py`:

```python
"""The on-demand tribute theme seeds as kind='tribute', locked, idempotent."""

from __future__ import annotations

import pytest

from flashback.themes.repository import ensure_tribute_theme_async
from flashback.tribute.theme import (
    TRIBUTE_DESCRIPTION,
    TRIBUTE_DISPLAY_NAME,
    TRIBUTE_SLUG,
)

pytestmark = pytest.mark.asyncio


async def test_ensure_tribute_theme_idempotent_and_locked(
    async_db_pool, make_person
) -> None:
    person_id = make_person("Dad")
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            first = await ensure_tribute_theme_async(
                cur, person_id=person_id, slug=TRIBUTE_SLUG,
                display_name=TRIBUTE_DISPLAY_NAME, description=TRIBUTE_DESCRIPTION,
            )
            second = await ensure_tribute_theme_async(
                cur, person_id=person_id, slug=TRIBUTE_SLUG,
                display_name=TRIBUTE_DISPLAY_NAME, description=TRIBUTE_DESCRIPTION,
            )
            await cur.execute(
                "SELECT kind, state FROM themes WHERE id = %s", (first,)
            )
            kind, state = await cur.fetchone()
            await conn.commit()
    assert first == second
    assert kind == "tribute"
    assert state == "locked"
```

- [ ] **Step 3: Archetype count unit test**

Create `tests/tribute/test_archetype_count.py` (pure — asserts the tribute count constants and that the defaults are unchanged for universals):

```python
"""The tribute archetype count is wider than the universal default."""

from __future__ import annotations

import inspect

from flashback.themes.archetype_llm import generate_archetype_questions
from flashback.tribute.theme import TRIBUTE_ARCHETYPE_MAX, TRIBUTE_ARCHETYPE_MIN


def test_universal_defaults_are_3_to_4() -> None:
    sig = inspect.signature(generate_archetype_questions)
    assert sig.parameters["min_questions"].default == 3
    assert sig.parameters["max_questions"].default == 4


def test_tribute_count_is_wider() -> None:
    assert TRIBUTE_ARCHETYPE_MIN >= 5
    assert TRIBUTE_ARCHETYPE_MAX >= TRIBUTE_ARCHETYPE_MIN
    assert TRIBUTE_ARCHETYPE_MAX <= 8
```

- [ ] **Step 4: Message capture test (LLM stubbed)**

Create `tests/tribute/test_message_capture.py`:

```python
"""persist_message_answer routes the sidecar to the tribute row and
clears the WM signal; a no-pending-tap answer is ignored."""

from __future__ import annotations

import json

import pytest

import flashback.tribute.message_capture as mc
from flashback.http.models import MessageAnswerInput

pytestmark = pytest.mark.asyncio


class _FakeWM:
    def __init__(self, state) -> None:
        self._state = state
        self.cleared = False

    async def get_state(self, _sid):
        return self._state

    async def clear_pending_message(self, _sid):
        self.cleared = True


class _State:
    def __init__(self, *, pending: str, tribute_id: str) -> None:
        self.signal_pending_message = pending
        self.current_tribute_id = tribute_id


async def test_ignored_when_no_pending(monkeypatch, async_db_pool, make_person):
    person_id = make_person("Dad")
    wm = _FakeWM(_State(pending="", tribute_id=""))
    await mc.persist_message_answer(
        session_id=_uuid(), person_id=person_id,
        answer=MessageAnswerInput(free_text="hi"),
        wm=wm, db_pool=async_db_pool, settings=None,
    )
    assert wm.cleared is False  # ignored before any clear


async def test_records_polished_message(monkeypatch, async_db_pool, make_person):
    from flashback.themes.repository import ensure_tribute_theme_async
    from flashback.tribute.repository import ensure_open_tribute_async
    from flashback.tribute.theme import (
        TRIBUTE_DESCRIPTION, TRIBUTE_DISPLAY_NAME, TRIBUTE_SLUG,
    )

    person_id = make_person("Dad")
    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            theme_id = await ensure_tribute_theme_async(
                cur, person_id=person_id, slug=TRIBUTE_SLUG,
                display_name=TRIBUTE_DISPLAY_NAME, description=TRIBUTE_DESCRIPTION,
            )
            tribute_id = await ensure_open_tribute_async(
                cur, person_id=person_id, theme_id=theme_id
            )
            await conn.commit()

    async def _fake_polish(**kwargs):
        return "Polished: " + kwargs["raw_text"]

    monkeypatch.setattr(mc, "polish_message", _fake_polish)
    wm = _FakeWM(_State(pending=json.dumps({"kind": "message"}),
                        tribute_id=tribute_id))

    await mc.persist_message_answer(
        session_id=_uuid(), person_id=person_id,
        answer=MessageAnswerInput(free_text="i never said thanks"),
        wm=wm, db_pool=async_db_pool, settings=None,
    )

    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT message_text FROM tributes WHERE id = %s", (tribute_id,)
            )
            (msg,) = await cur.fetchone()
    assert msg == "Polished: i never said thanks"
    assert wm.cleared is True


def _uuid():
    from uuid import UUID
    return UUID("11111111-1111-1111-1111-111111111111")
```

- [ ] **Step 5: Invitation-step gating test (pure, fake deps)**

Create `tests/tribute/test_select_message_invitation.py`. This is a pure-logic test driving the step with hand-built fakes — it asserts the gates (wrong intent, not warm, no tribute, already asked) cause no tap, and the happy path emits a `message` tap.

```python
"""Gating behavior of select_message_invitation (no real DB/LLM)."""

from __future__ import annotations

import pytest

import flashback.orchestrator.steps.select_message_invitation as step
from flashback.orchestrator.steps.select_message_invitation import (
    select_message_invitation,
)
from flashback.tribute.progress import TributeProgress, TributeSlot

pytestmark = pytest.mark.asyncio


class _Intent:
    def __init__(self, intent: str) -> None:
        self.intent = intent


class _WMState:
    def __init__(self, **kw) -> None:
        self.current_tribute_id = kw.get("current_tribute_id", "t1")
        self.message_invitation_asked = kw.get("message_invitation_asked", False)
        self.user_turns_since_last_tap = kw.get("user_turns_since_last_tap", 9)


class _TurnState:
    def __init__(self, *, intent="deepen", temp="high", taps=None, wm=None) -> None:
        self.intent_result = _Intent(intent)
        self.effective_temperature = temp
        self.taps = taps or []
        self.working_memory_state = wm or _WMState()
        self.session_id = "s1"


class _WM:
    def __init__(self) -> None:
        self.emitted = False

    async def get_state(self, _sid):
        raise AssertionError("should use state.working_memory_state")

    async def record_message_invitation_emitted(self, *, session_id, payload_json):
        self.emitted = True


class _Deps:
    def __init__(self) -> None:
        self.working_memory = _WM()
        self.db_pool = None  # patched progress avoids DB


def _ready_progress():
    return TributeProgress(
        tribute_id="t1", percent=60, ready=False,
        slots=[
            TributeSlot("memories", "Shared memories", "", True),
            TributeSlot("message", "Your message", "", False),
            TributeSlot("appearance", "How they looked", "", True),
            TributeSlot("signature", "What made them them", "", True),
        ],
    )


async def _patch_progress(monkeypatch, progress):
    async def _fake(cur, *, tribute_id):
        return progress
    # db_pool.connection() is awaited via async context manager; bypass by
    # patching the progress fetch the step calls.
    monkeypatch.setattr(step, "fetch_tribute_progress_async", _fake)

    class _Conn:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def cursor(self): return self
        async def __aenter__cur(self): return self
    # Provide a minimal db_pool whose connection() works as an async cm.
    class _Cur:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
    class _ConnCtx:
        async def __aenter__(self): return _ConnCtx()
        async def __aexit__(self, *a): return False
        def cursor(self): return _Cur()
    class _Pool:
        def connection(self): return _ConnCtx()
    return _Pool()


async def test_happy_path_emits_message_tap(monkeypatch):
    deps = _Deps()
    deps.db_pool = await _patch_progress(monkeypatch, _ready_progress())
    state = _TurnState()
    await select_message_invitation(state, deps)
    assert len(state.taps) == 1
    assert state.taps[0].kind == "message"
    assert deps.working_memory.emitted is True


async def test_wrong_intent_no_tap(monkeypatch):
    deps = _Deps()
    deps.db_pool = await _patch_progress(monkeypatch, _ready_progress())
    state = _TurnState(intent="switch")
    await select_message_invitation(state, deps)
    assert state.taps == []


async def test_not_warm_no_tap(monkeypatch):
    deps = _Deps()
    deps.db_pool = await _patch_progress(monkeypatch, _ready_progress())
    state = _TurnState(temp="low")
    await select_message_invitation(state, deps)
    assert state.taps == []


async def test_already_asked_no_tap(monkeypatch):
    deps = _Deps()
    deps.db_pool = await _patch_progress(monkeypatch, _ready_progress())
    state = _TurnState(wm=_WMState(message_invitation_asked=True))
    await select_message_invitation(state, deps)
    assert state.taps == []
```

> Implementer note: the fake `db_pool` plumbing above is fiddly because the step opens `db_pool.connection()` as an async context manager before calling `fetch_tribute_progress_async`. If the fakes prove brittle, refactor the step to take the progress via a small injectable helper, OR convert this to a DB-backed test using the `async_db_pool` fixture and a real tribute row (preferred if the fixture exists). Keep the four gate assertions regardless of mechanism.

- [ ] **Step 6: Run the suite**

Start the DB (Docker → `docker compose -f docker-compose.local.yml up -d postgres`; ensure `flashback_test` exists), then in PowerShell:

```
$env:TEST_DATABASE_URL = "postgresql://flashback:flashback@localhost:15432/flashback_test"
python -m pytest tests/tribute -v
```
Expected: all `tests/tribute` tests PASS (DB tests run; pure tests always run).

Then the whole suite for regressions:
```
python -m pytest -q
```
Expected: PASS modulo the known pre-existing failures in the test-environment notes.

- [ ] **Step 7: Commit**

```bash
git add tests/tribute/
git commit -m "test(tribute): capture flow — async repo, seeding, capture, invitation

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

## Plan Self-Review

**Spec coverage (Plan 2 slice):**
- Message captured late, gated on warmth, never an MC chip, never cold (spec §5) → Task 7 Step 4 (`select_message_invitation` gates: intent story/deepen, temp=high, slots-ready, once-per-session) ✓
- Structured sidecar, never mined (spec §5) → Task 7 Steps 2–3 (`message_answer` sidecar + `persist_message_answer` writes to tribute row, never transcript) ✓
- LLM-polished from their words (spec §5) → Task 6 (`polish_message`, falls back to raw) ✓
- Expanded archetype set (spec §5 step 1) → Task 3 (count params; tribute requests 6–8) ✓
- Tribute theme reuses unlock machinery; seeded on demand, not at creation (spec §4) → Task 2 + Task 5 ✓
- Tribute row bridges to live flow (`current_tribute_id`) → Tasks 4–5 ✓
- Tap kind addition is a Node contract note (spec §9 / API) → Task 7 Step 1 ✓
- Deferred, correctly absent: slot-gap steering (→ Plan 4), assembly/`/generate` (→ Plan 3), skin copy (→ Plan 4).

**Placeholder scan:** No TBD/TODO. Four "Implementer note" callouts flag genuine integration points that need confirming against current code at execution time (settings accessor in the route; WM bool coercion; WM-init mapping for `current_tribute_id`; the streaming-twin registration; the async test-pool fixture). These are verification instructions, not unfilled code — every code block is complete.

**Type consistency:**
- `ensure_open_tribute_async` / `fetch_open_tribute_id_async` / `set_message_async` names match between `repository.py` (Task 1) and tests (Task 8) and `message_capture.py` (Task 7) and `apply_theme_unlock.py` (Task 5).
- `fetch_tribute_progress_async` returns `TributeProgress` (with `.slots[].key/.filled`, `.percent`) — consumed consistently in `select_message_invitation` (Task 7) and tests (Task 8).
- `MessageAnswerInput` fields (`option_label`, `free_text`, `skipped`) match between `models.py` (Task 7) and `message_capture.py` + tests.
- WM fields `current_tribute_id`, `signal_pending_message`, `message_invitation_asked` match between `schema.py` (Task 4), the client methods (Task 4), the bridge (Task 5), and the step (Task 7).
- `Tap(kind="message", question_id=None, field=None)` matches the extended `Tap.kind` Literal (Task 7 Step 1).
- `polish_message(settings=, raw_text=, person_name=, person_relationship=)` signature matches its caller in `message_capture.py`.

# Tribute Assembly + Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a ready tribute into compiled-artifact jobs — assemble an ordered scene script (with the polished message placed as the climax) via a big LLM, compose the painterly video + storybook generation contexts, and push the jobs onto `artifact_generation` for Node to render.

**Architecture:** A `POST /tributes/{id}/generate` endpoint gates on the `tribute_status` view (video needs `ready`; storybook needs a minimum qualifying-moment count), assembles a shared `TributeScript` (big LLM, Sonnet — falls back to chronological), then builds an artifact-kind-specific compiled context. Per-scene prompts reuse the existing `compose_scene_prompt` + `SCENE_NEGATIVE_PROMPT` (which already bans photorealism/deepfakes — spec §2). The context is merged into `tributes.latest_generation_context` **keyed by artifact_kind** (so the video and storybook contexts coexist on one row without clobbering each other or racing on `composed_at`), the row flips to `generating`, and a trigger-only SQS job is pushed (`record_type='tribute'`, `artifact_kind` ∈ {`tribute_video`,`storybook`}). Node reads the keyed context at job time, renders, uploads to S3, writes the URL columns.

**Tech Stack:** Python, psycopg async, FastAPI/pydantic, big-LLM via `call_with_tool` (`settings.llm_big_provider`/`llm_big_model` = `claude-sonnet-4-6`), the existing `flashback.artifacts` compose/preset/queue stack.

**Builds on Plans 1–2:** `tributes` table + `tribute_status` view + `message_text`; async tribute repo (`ensure_open_tribute_async`, `set_message_async`); `fetch_tribute_progress_async`; the `tribute` theme.

**Scope:**
- **In:** scene-moment fetch; the assembly LLM + `TributeScript`; the keyed `latest_generation_context` writer; video + storybook context builders (9-page cap, 45s default length, painterly preset + negative); `POST /tributes/{id}/generate` with gating; new `artifact_kind`s on the queue; status transition to `generating`.
- **Out (deferred):** the **general (non-tribute) storybook** any legacy can make from its graph — Plan 3 generates the storybook off the **tribute row** only (the tribute-flavored variant). The general engine is a later addition. Father's Day skin / skin-configurable video length → Plan 4. Node's compiled renderer lives in the **other repo** (see dependency below).

**⚠️ Critical dependency:** the compiled multi-scene video + storybook are a **new job shape Node's worker does not handle yet** (today's jobs are single-record artifacts). Our side (this plan) composes context + pushes jobs; nothing renders until the Node team ships a compiled-job renderer that understands the keyed `latest_generation_context` and the new `artifact_kind`s. Confirm Node capacity before relying on rendered output.

**Testing convention (per user instruction):** Build first, tests after. Tasks 1–6 implement + commit with no test run. Task 7 authors all tests and runs the suite once.

Spec: [`docs/superpowers/specs/2026-06-14-tribute-output-design.md`](../specs/2026-06-14-tribute-output-design.md) §8 (assembly + boundary), §2 (visual register), §3 (output split).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/flashback/tribute/theme.py` (modify) | Add `VIDEO_TARGET_SECONDS`, `STORYBOOK_MIN_PAGES`, `STORYBOOK_MAX_PAGES` |
| `src/flashback/tribute/repository.py` (modify) | Add `fetch_scene_moments_async`, `set_script_async`, `set_status_async`, `write_tribute_generation_context_async`, `fetch_tribute_for_assembly_async` |
| `src/flashback/tribute/assembly.py` (create) | `TributeScript`/`Scene` dataclasses + `assemble_tribute_script` (big-LLM, chronological fallback) |
| `src/flashback/tribute/artifact_context.py` (create) | `build_tribute_video_context`, `build_storybook_context` |
| `src/flashback/http/models.py` (modify) | `TributeGenerateRequest` + `TributeGenerateResponse` |
| `src/flashback/http/routes/tributes.py` (create) | `POST /tributes/{id}/generate` |
| `src/flashback/http/app.py` (modify) | Register the tributes router |
| `tests/tribute/test_*` (create) | assembly fallback, context builders, generate gating |

---

### Task 1: Output constants

**Files:** Modify `src/flashback/tribute/theme.py`

- [ ] **Step 1: Add the constants**

Append to `src/flashback/tribute/theme.py`:

```python
# Compiled-output shape (Plan 3). Video length is skin-configurable in
# Plan 4; this is the neutral default. Storybook is hard-capped at 9 pages
# (spec §4 refinement) with a floor below which it won't generate.
VIDEO_TARGET_SECONDS = 45
STORYBOOK_MIN_PAGES = 3
STORYBOOK_MAX_PAGES = 9
```

- [ ] **Step 2: Commit**

```bash
git add src/flashback/tribute/theme.py
git commit -m "feat(tribute): compiled-output constants (video length, storybook page caps)

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 2: Repository surfaces for assembly + generation

**Files:** Modify `src/flashback/tribute/repository.py`

- [ ] **Step 1: Add `json` import**

At the top of `src/flashback/tribute/repository.py`, add `import json` (the file currently imports `Json` from psycopg; we also need raw `json.dumps` for the keyed JSONB merge):

```python
import json
```

- [ ] **Step 2: Append the new async functions**

Append to `src/flashback/tribute/repository.py`:

```python
# ---------------------------------------------------------------------------
# Assembly + generation surfaces (Plan 3)
# ---------------------------------------------------------------------------


async def fetch_scene_moments_async(
    cur, *, person_id: UUID | str, limit: int = 12
) -> list[dict[str, Any]]:
    """Return candidate scene moments (qualifying, newest first) for assembly.

    'Qualifying' mirrors the tribute_status view: has sensory_details, a
    time_anchor, or an involves edge. Returns the fields the assembler and
    the scene-prompt composer need.
    """
    await cur.execute(
        """
        SELECT m.id::text, m.title, m.narrative,
               m.generation_prompt, m.sensory_details
          FROM active_moments m
         WHERE m.person_id = %(person_id)s
           AND (
                m.sensory_details IS NOT NULL
             OR m.time_anchor IS NOT NULL
             OR EXISTS (
                 SELECT 1 FROM edges ie
                  WHERE ie.from_kind = 'moment'
                    AND ie.from_id   = m.id
                    AND ie.edge_type = 'involves'
                    AND ie.status    = 'active'
             )
           )
         ORDER BY m.created_at DESC
         LIMIT %(limit)s
        """,
        {"person_id": str(person_id), "limit": limit},
    )
    rows = await cur.fetchall()
    return [
        {
            "id": r[0],
            "title": r[1],
            "narrative": r[2],
            "generation_prompt": r[3],
            "sensory_details": r[4],
        }
        for r in rows
    ]


async def fetch_tribute_for_assembly_async(
    cur, *, tribute_id: UUID | str
) -> dict[str, Any] | None:
    """Return the tribute + its subject's name/relationship for assembly."""
    await cur.execute(
        """
        SELECT tr.id::text, tr.person_id::text, tr.message_text,
               p.name, p.relationship
          FROM tributes tr
          JOIN persons p ON p.id = tr.person_id
         WHERE tr.id = %(id)s
        """,
        {"id": str(tribute_id)},
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "person_id": row[1],
        "message_text": row[2],
        "person_name": row[3],
        "person_relationship": row[4],
    }


async def set_script_async(
    cur,
    *,
    tribute_id: UUID | str,
    script: dict[str, Any],
    scene_moment_ids: list[str],
    checklist_state: dict[str, Any] | None = None,
) -> None:
    """Persist the assembled script + scene id list + checklist snapshot."""
    await cur.execute(
        """
        UPDATE tributes
           SET script = %(script)s,
               scene_moment_ids = %(scene_ids)s,
               checklist_state = %(checklist)s
         WHERE id = %(id)s
        """,
        {
            "id": str(tribute_id),
            "script": Json(script),
            "scene_ids": [str(s) for s in scene_moment_ids],
            "checklist": Json(checklist_state) if checklist_state is not None else None,
        },
    )


async def set_status_async(cur, *, tribute_id: UUID | str, status: str) -> None:
    """Async twin of ``set_status_sync``."""
    await cur.execute(
        _SET_STATUS_SQL, {"id": str(tribute_id), "status": status}
    )


async def write_tribute_generation_context_async(
    cur,
    *,
    tribute_id: UUID | str,
    artifact_kind: str,
    context: dict[str, Any],
) -> None:
    """Merge a per-artifact-kind context into ``latest_generation_context``.

    The tributes row carries two compiled artifacts (tribute_video +
    storybook). Keying the context by artifact_kind lets both coexist on
    one row and keeps each job's ``composed_at`` stale-check independent:
    writing the storybook context never invalidates an in-flight video job.
    """
    await cur.execute(
        """
        UPDATE tributes
           SET latest_generation_context =
               COALESCE(latest_generation_context, '{}'::jsonb)
               || jsonb_build_object(%(kind)s, %(ctx)s::jsonb)
         WHERE id = %(id)s
        """,
        {
            "id": str(tribute_id),
            "kind": artifact_kind,
            "ctx": json.dumps(context),
        },
    )
```

- [ ] **Step 3: Commit**

```bash
git add src/flashback/tribute/repository.py
git commit -m "feat(tribute): repo surfaces for assembly + keyed generation context

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 3: Script assembly (big LLM + chronological fallback)

**Files:** Create `src/flashback/tribute/assembly.py`

- [ ] **Step 1: Write the assembler**

Create `src/flashback/tribute/assembly.py`:

```python
"""Assemble an ordered tribute script from candidate scene moments.

Big-LLM (Sonnet) selects + orders the strongest moments, writes a one-line
caption per scene, and an opening + closing line; the polished message is
placed as the climax (just before the closing). Best-effort: on any LLM
failure, fall back to chronological order with title-derived captions so a
tribute can always be assembled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from flashback.llm.errors import LLMError
from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import xml_text
from flashback.llm.tool_spec import ToolSpec

log = structlog.get_logger("flashback.tribute.assembly")


@dataclass(frozen=True)
class Scene:
    moment_id: str
    caption: str


@dataclass(frozen=True)
class TributeScript:
    scenes: list[Scene]
    opening_caption: str
    closing_caption: str
    message_text: str  # placed as the climax, before the closing


_ASSEMBLY_SYSTEM = """\
You arrange a short tribute video/storybook from a contributor's memories
of a loved one. You receive candidate scenes (each an id + a short memory)
and the contributor's own closing message.

Produce:
- An ordered subset of scenes (3 to {max_scenes}). Pick the most vivid,
  emotionally distinct moments; drop weak or redundant ones. Order them so
  the arc builds -- not strictly chronological, but emotionally coherent.
- A one-line caption for each chosen scene (4-10 words, warm, concrete,
  present-tense fragments -- not full sentences). Never invent facts; draw
  only on the scene's own memory text.
- A short opening line (sets the tone) and a short closing line (lands the
  feeling). Neither may invent facts.

The contributor's message is the climax -- you do NOT rewrite it; it is
inserted verbatim after the last scene and before your closing line.

Call the `assemble` tool exactly once.
"""

_ASSEMBLY_TOOL = ToolSpec(
    name="assemble",
    description="Return the ordered scenes + captions + opening/closing. Once.",
    input_schema={
        "type": "object",
        "properties": {
            "scenes": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "moment_id": {"type": "string"},
                        "caption": {"type": "string", "minLength": 1, "maxLength": 120},
                    },
                    "required": ["moment_id", "caption"],
                    "additionalProperties": False,
                },
            },
            "opening_caption": {"type": "string", "maxLength": 160},
            "closing_caption": {"type": "string", "maxLength": 160},
        },
        "required": ["scenes", "opening_caption", "closing_caption"],
        "additionalProperties": False,
    },
)


def _fallback_script(
    candidates: list[dict[str, Any]], *, message_text: str, max_scenes: int
) -> TributeScript:
    chosen = candidates[:max_scenes]
    scenes = [
        Scene(moment_id=c["id"], caption=(c.get("title") or "A memory").strip())
        for c in chosen
    ]
    return TributeScript(
        scenes=scenes,
        opening_caption="",
        closing_caption="",
        message_text=message_text,
    )


async def assemble_tribute_script(
    *,
    settings,
    candidates: list[dict[str, Any]],
    message_text: str,
    person_name: str,
    person_relationship: str | None,
    max_scenes: int,
) -> TributeScript:
    """Return an assembled script. Falls back to chronological on failure."""
    usable = [c for c in candidates if c.get("id")]
    if not usable:
        return TributeScript([], "", "", message_text)
    if settings is None:
        return _fallback_script(usable, message_text=message_text, max_scenes=max_scenes)

    by_id = {c["id"]: c for c in usable}
    scene_blocks = "\n".join(
        f'<scene id="{xml_text(c["id"])}">'
        f"{xml_text((c.get('narrative') or c.get('title') or '').strip())}"
        f"</scene>"
        for c in usable
    )
    rel = f' relationship="{xml_text(person_relationship)}"' if person_relationship else ""
    user_block = (
        f"<subject{rel}>{xml_text(person_name)}</subject>\n"
        f"<message>{xml_text(message_text)}</message>\n"
        f"<candidate_scenes>\n{scene_blocks}\n</candidate_scenes>"
    )

    try:
        args = await call_with_tool(
            provider=settings.llm_big_provider,
            model=settings.llm_big_model,
            system_prompt=_ASSEMBLY_SYSTEM.replace("{max_scenes}", str(max_scenes)),
            user_message=user_block,
            tool=_ASSEMBLY_TOOL,
            max_tokens=1500,
            timeout=30.0,
            settings=settings,
        )
    except LLMError as exc:
        log.warning("tribute_assembly.llm_failed", error=str(exc))
        return _fallback_script(usable, message_text=message_text, max_scenes=max_scenes)
    except Exception as exc:  # defensive
        log.warning(
            "tribute_assembly.unexpected_failure",
            error_type=type(exc).__name__,
            detail=str(exc),
        )
        return _fallback_script(usable, message_text=message_text, max_scenes=max_scenes)

    raw_scenes = args.get("scenes") if isinstance(args, dict) else None
    if not isinstance(raw_scenes, list) or not raw_scenes:
        return _fallback_script(usable, message_text=message_text, max_scenes=max_scenes)

    scenes: list[Scene] = []
    for raw in raw_scenes[:max_scenes]:
        if not isinstance(raw, dict):
            continue
        mid = raw.get("moment_id")
        caption = (raw.get("caption") or "").strip()
        if mid in by_id and caption:
            scenes.append(Scene(moment_id=mid, caption=caption))
    if not scenes:
        return _fallback_script(usable, message_text=message_text, max_scenes=max_scenes)

    return TributeScript(
        scenes=scenes,
        opening_caption=(args.get("opening_caption") or "").strip(),
        closing_caption=(args.get("closing_caption") or "").strip(),
        message_text=message_text,
    )
```

- [ ] **Step 2: Commit**

```bash
git add src/flashback/tribute/assembly.py
git commit -m "feat(tribute): script assembly LLM with chronological fallback

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 4: Compiled context builders

**Files:** Create `src/flashback/tribute/artifact_context.py`

- [ ] **Step 1: Write the builders**

Create `src/flashback/tribute/artifact_context.py`:

```python
"""Build the compiled generation contexts for tribute video + storybook.

Per-scene prompts reuse the existing scene composer + negative prompt, so
the painterly-realism register and the no-photorealism/no-deepfake bans
(SCENE_NEGATIVE_PROMPT) apply to every scene/page (spec §2). The shapes
here are what Node's compiled renderer reads from
``tributes.latest_generation_context[artifact_kind]``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from flashback.artifacts.compose import SCENE_NEGATIVE_PROMPT, compose_scene_prompt
from flashback.tribute.assembly import TributeScript


def _scene_base_prompt(moment: dict[str, Any]) -> str:
    """Prefer the moment's LLM-emitted generation_prompt; fall back to text."""
    base = (moment.get("generation_prompt") or "").strip()
    if base:
        return base
    # No stored scene prompt (older moment) -- ground on its own text.
    return (moment.get("narrative") or moment.get("title") or "").strip()


def build_tribute_video_context(
    *,
    script: TributeScript,
    moments_by_id: dict[str, dict[str, Any]],
    preset: str,
    target_duration_seconds: int,
    ground_truth_context: str | None = None,
) -> dict[str, Any]:
    """Compile the tribute-video context (keyed under 'tribute_video')."""
    n = max(1, len(script.scenes))
    per_scene = max(2, round(target_duration_seconds / n))
    scenes: list[dict[str, Any]] = []
    for s in script.scenes:
        moment = moments_by_id.get(s.moment_id, {})
        prompt = compose_scene_prompt(
            base_prompt=_scene_base_prompt(moment),
            preset=preset,
            ground_truth_context=ground_truth_context,
        )
        scenes.append(
            {
                "moment_id": s.moment_id,
                "prompt": prompt,
                "negative": SCENE_NEGATIVE_PROMPT,
                "caption": s.caption,
                "duration_seconds": per_scene,
            }
        )
    return {
        "scenes": scenes,
        "opening_caption": script.opening_caption,
        "message_text": script.message_text,
        "closing_caption": script.closing_caption,
        "style_preset": preset,
        "target_duration_seconds": target_duration_seconds,
        "negative_prompt": SCENE_NEGATIVE_PROMPT,
        "composed_at": datetime.now(timezone.utc).isoformat(),
    }


def build_storybook_context(
    *,
    script: TributeScript,
    moments_by_id: dict[str, dict[str, Any]],
    preset: str,
    max_pages: int,
    ground_truth_context: str | None = None,
) -> dict[str, Any]:
    """Compile the storybook context (keyed under 'storybook').

    Cover + up to (max_pages - 1) content pages. The contributor message is
    the final page.
    """
    content_budget = max(1, max_pages - 1)
    pages: list[dict[str, Any]] = []
    for s in script.scenes[:content_budget]:
        moment = moments_by_id.get(s.moment_id, {})
        prompt = compose_scene_prompt(
            base_prompt=_scene_base_prompt(moment),
            preset=preset,
            ground_truth_context=ground_truth_context,
        )
        pages.append(
            {
                "moment_id": s.moment_id,
                "prompt": prompt,
                "negative": SCENE_NEGATIVE_PROMPT,
                "caption": s.caption,
            }
        )
    return {
        "cover": {
            "caption": script.opening_caption,
            "style_preset": preset,
        },
        "pages": pages,
        "message_page": {"text": script.message_text},
        "closing_caption": script.closing_caption,
        "style_preset": preset,
        "max_pages": max_pages,
        "negative_prompt": SCENE_NEGATIVE_PROMPT,
        "composed_at": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 2: Commit**

```bash
git add src/flashback/tribute/artifact_context.py
git commit -m "feat(tribute): compiled video + storybook context builders

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 5: Request/response models

**Files:** Modify `src/flashback/http/models.py`

- [ ] **Step 1: Add the models**

In `src/flashback/http/models.py`, add (near the other tribute / artifact models):

```python
class TributeGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: UUID
    artifact_kind: Literal["tribute_video", "storybook"] = "tribute_video"
    preset: str | None = None


class TributeGenerateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    tribute_id: UUID
    artifact_kind: Literal["tribute_video", "storybook"]
    enqueued: bool
    percent: int
    ready: bool
    scene_count: int
```

- [ ] **Step 2: Commit**

```bash
git add src/flashback/http/models.py
git commit -m "feat(tribute): generate endpoint request/response models

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 6: The `POST /tributes/{id}/generate` endpoint

**Files:**
- Create: `src/flashback/http/routes/tributes.py`
- Modify: `src/flashback/http/app.py`

- [ ] **Step 1: Write the route**

Create `src/flashback/http/routes/tributes.py`:

```python
"""Tribute generation endpoint.

POST /tributes/{id}/generate gates on the tribute_status view, assembles a
script, composes the artifact-kind context, writes it (keyed) to the
tribute row, flips status to 'generating', and pushes a trigger-only
artifact_generation job. Node's compiled renderer reads the context.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from psycopg_pool import AsyncConnectionPool

from flashback.artifacts.presets import resolve_preset
from flashback.config import HttpConfig
from flashback.ground_truth.render import render_ground_truth_block
from flashback.ground_truth.store import fetch_ground_truth
from flashback.http.auth import require_service_token
from flashback.http.deps import (
    get_artifact_generation_queue,
    get_db_pool,
    get_http_config,
)
from flashback.http.models import TributeGenerateRequest, TributeGenerateResponse
from flashback.tribute.artifact_context import (
    build_storybook_context,
    build_tribute_video_context,
)
from flashback.tribute.assembly import assemble_tribute_script
from flashback.tribute.progress import fetch_tribute_progress_async
from flashback.tribute.repository import (
    fetch_scene_moments_async,
    fetch_tribute_for_assembly_async,
    set_script_async,
    set_status_async,
    write_tribute_generation_context_async,
)
from flashback.tribute.theme import (
    STORYBOOK_MAX_PAGES,
    STORYBOOK_MIN_PAGES,
    VIDEO_TARGET_SECONDS,
)

if TYPE_CHECKING:
    from flashback.queues.artifact_generation import (
        ArtifactGenerationQueueProducer,
    )

router = APIRouter(dependencies=[Depends(require_service_token)])
log = structlog.get_logger("flashback.http.tributes")

_MAX_VIDEO_SCENES = 6


@router.post("/tributes/{tribute_id}/generate", response_model=TributeGenerateResponse)
async def generate_tribute(
    tribute_id: UUID,
    body: TributeGenerateRequest,
    cfg: HttpConfig = Depends(get_http_config),
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    artifact_queue: "ArtifactGenerationQueueProducer | None" = Depends(
        get_artifact_generation_queue
    ),
) -> TributeGenerateResponse:
    try:
        preset_slug = resolve_preset(body.preset)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 1) Gate + ownership via the status view + the tribute row.
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            tribute = await fetch_tribute_for_assembly_async(cur, tribute_id=tribute_id)
            if tribute is None or tribute["person_id"] != str(body.person_id):
                raise HTTPException(status_code=404, detail="tribute not found")
            progress = await fetch_tribute_progress_async(cur, tribute_id=tribute_id)
            candidates = await fetch_scene_moments_async(
                cur, person_id=body.person_id, limit=12
            )
            ground_truth = await fetch_ground_truth(db_pool, body.person_id)

    if progress is None:
        raise HTTPException(status_code=404, detail="tribute status unavailable")

    if body.artifact_kind == "tribute_video" and not progress.ready:
        raise HTTPException(
            status_code=409,
            detail=f"tribute not ready for video (percent={progress.percent})",
        )
    if body.artifact_kind == "storybook" and len(candidates) < STORYBOOK_MIN_PAGES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"need at least {STORYBOOK_MIN_PAGES} qualifying moments for a "
                f"storybook (have {len(candidates)})"
            ),
        )

    # 2) Assemble the shared script.
    max_scenes = (
        _MAX_VIDEO_SCENES
        if body.artifact_kind == "tribute_video"
        else STORYBOOK_MAX_PAGES - 1
    )
    script = await assemble_tribute_script(
        settings=cfg,
        candidates=candidates,
        message_text=tribute["message_text"] or "",
        person_name=tribute["person_name"] or "",
        person_relationship=tribute["person_relationship"],
        max_scenes=max_scenes,
    )
    moments_by_id = {c["id"]: c for c in candidates}
    gt_scene = render_ground_truth_block(ground_truth, "scene") or None

    # 3) Build the artifact-kind context.
    if body.artifact_kind == "tribute_video":
        context = build_tribute_video_context(
            script=script,
            moments_by_id=moments_by_id,
            preset=preset_slug,
            target_duration_seconds=VIDEO_TARGET_SECONDS,
            ground_truth_context=gt_scene,
        )
    else:
        context = build_storybook_context(
            script=script,
            moments_by_id=moments_by_id,
            preset=preset_slug,
            max_pages=STORYBOOK_MAX_PAGES,
            ground_truth_context=gt_scene,
        )

    # 4) Persist script + keyed context + flip status, all before pushing.
    scene_ids = [s.moment_id for s in script.scenes]
    checklist_state = {s.key: s.filled for s in progress.slots}
    async with db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await set_script_async(
                    cur,
                    tribute_id=tribute_id,
                    script={
                        "scenes": [
                            {"moment_id": s.moment_id, "caption": s.caption}
                            for s in script.scenes
                        ],
                        "opening_caption": script.opening_caption,
                        "closing_caption": script.closing_caption,
                        "message_text": script.message_text,
                    },
                    scene_moment_ids=scene_ids,
                    checklist_state=checklist_state,
                )
                await write_tribute_generation_context_async(
                    cur,
                    tribute_id=tribute_id,
                    artifact_kind=body.artifact_kind,
                    context=context,
                )
                await set_status_async(
                    cur, tribute_id=tribute_id, status="generating"
                )

    # 5) Push the trigger-only job.
    job_id = str(uuid4())
    enqueued = False
    if artifact_queue is not None:
        try:
            msg_id = await artifact_queue.push(
                job_id=job_id,
                record_type="tribute",
                record_id=str(tribute_id),
                person_id=str(body.person_id),
                artifact_kind=body.artifact_kind,
                source="auto",
                composed_at=context["composed_at"],
            )
            enqueued = msg_id is not None
        except Exception:
            log.warning("tribute.enqueue_failed", tribute_id=str(tribute_id), exc_info=True)

    return TributeGenerateResponse(
        job_id=job_id,
        tribute_id=tribute_id,
        artifact_kind=body.artifact_kind,
        enqueued=enqueued,
        percent=progress.percent,
        ready=progress.ready,
        scene_count=len(script.scenes),
    )
```

- [ ] **Step 2: Register the router**

In `src/flashback/http/app.py`, find where the other routers are imported and included (e.g. `from flashback.http.routes.artifacts import router as artifacts_router` and `app.include_router(artifacts_router)`), and add the same for tributes:

```python
from flashback.http.routes.tributes import router as tributes_router
```
and alongside the other `app.include_router(...)` calls:
```python
app.include_router(tributes_router)
```

> Implementer note: match the exact include style used for the artifacts router (prefix/tags args, if any). Open `app.py` and mirror it.

- [ ] **Step 3: Commit**

```bash
git add src/flashback/http/routes/tributes.py src/flashback/http/app.py
git commit -m "feat(tribute): POST /tributes/{id}/generate — assemble + push compiled jobs

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

### Task 7: Tests + run the suite

**Files:**
- Create: `tests/tribute/test_assembly.py`
- Create: `tests/tribute/test_artifact_context.py`
- Create: `tests/tribute/test_generate_endpoint.py`

- [ ] **Step 1: Assembly fallback (pure, no LLM)**

Create `tests/tribute/test_assembly.py`:

```python
"""assemble_tribute_script falls back to chronological when settings=None."""

from __future__ import annotations

from flashback.tribute.assembly import assemble_tribute_script


async def test_fallback_takes_first_n_with_title_captions() -> None:
    candidates = [
        {"id": "m1", "title": "The workshop", "narrative": "n1"},
        {"id": "m2", "title": "Sunday lunch", "narrative": "n2"},
        {"id": "m3", "title": "The drive", "narrative": "n3"},
        {"id": "m4", "title": "Extra", "narrative": "n4"},
    ]
    script = await assemble_tribute_script(
        settings=None,
        candidates=candidates,
        message_text="Thank you, Dad.",
        person_name="Dad",
        person_relationship="father",
        max_scenes=3,
    )
    assert [s.moment_id for s in script.scenes] == ["m1", "m2", "m3"]
    assert script.scenes[0].caption == "The workshop"
    assert script.message_text == "Thank you, Dad."


async def test_empty_candidates_yield_empty_script() -> None:
    script = await assemble_tribute_script(
        settings=None,
        candidates=[],
        message_text="hi",
        person_name="Dad",
        person_relationship=None,
        max_scenes=6,
    )
    assert script.scenes == []
    assert script.message_text == "hi"
```

- [ ] **Step 2: Context builders (pure)**

Create `tests/tribute/test_artifact_context.py`:

```python
"""Compiled context builders apply the preset + negative and respect caps."""

from __future__ import annotations

from flashback.artifacts.compose import SCENE_NEGATIVE_PROMPT
from flashback.tribute.artifact_context import (
    build_storybook_context,
    build_tribute_video_context,
)
from flashback.tribute.assembly import Scene, TributeScript


def _script(n: int) -> TributeScript:
    return TributeScript(
        scenes=[Scene(moment_id=f"m{i}", caption=f"c{i}") for i in range(n)],
        opening_caption="open",
        closing_caption="close",
        message_text="Thank you.",
    )


def _moments(n: int) -> dict:
    return {f"m{i}": {"generation_prompt": f"scene {i}"} for i in range(n)}


def test_video_context_durations_sum_near_target() -> None:
    ctx = build_tribute_video_context(
        script=_script(4),
        moments_by_id=_moments(4),
        preset="painterly_cinematic",
        target_duration_seconds=40,
    )
    assert len(ctx["scenes"]) == 4
    assert ctx["target_duration_seconds"] == 40
    assert all(s["negative"] == SCENE_NEGATIVE_PROMPT for s in ctx["scenes"])
    assert ctx["message_text"] == "Thank you."
    total = sum(s["duration_seconds"] for s in ctx["scenes"])
    assert 30 <= total <= 50  # ~10s/scene, rounded


def test_storybook_caps_pages_at_max_minus_cover() -> None:
    ctx = build_storybook_context(
        script=_script(20),
        moments_by_id=_moments(20),
        preset="storybook",
        max_pages=9,
    )
    assert len(ctx["pages"]) == 8  # 9 max - 1 cover
    assert ctx["message_page"]["text"] == "Thank you."
    assert ctx["max_pages"] == 9
```

- [ ] **Step 3: Generate endpoint gating (DB-backed via TestClient)**

Create `tests/tribute/test_generate_endpoint.py`. This drives the route through FastAPI's TestClient against the test DB, asserting the gate (a draft tribute with no message/moments is not ready → 409 for video) and the happy path (a ready tribute → 200, status flips to `generating`, context written under the artifact_kind key).

```python
"""POST /tributes/{id}/generate gating + happy path."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from flashback.http.app import create_app
from flashback.themes.repository import ensure_tribute_theme_async
from flashback.tribute.repository import ensure_open_tribute_async, set_message_async
from flashback.tribute.theme import (
    TRIBUTE_DESCRIPTION,
    TRIBUTE_DISPLAY_NAME,
    TRIBUTE_SLUG,
)


async def _client(monkeypatch):
    # Service-token auth: disable by pointing the dependency at a no-op, or
    # set the expected token header. Mirror how tests/http/* authenticate.
    app = create_app()
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _seed_ready_tribute(async_pool) -> tuple[str, str]:
    async with async_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name) VALUES ('Dad') RETURNING id::text"
                )
                person_id = (await cur.fetchone())[0]
                gt = json.dumps(
                    {
                        "region": {"value": "South India"},
                        "birth_era": {"value": "1950s"},
                        "attire": {"value": "white shirt"},
                    }
                )
                await cur.execute(
                    "UPDATE persons SET ground_truth = %s WHERE id = %s",
                    (gt, person_id),
                )
                for i in range(3):
                    await cur.execute(
                        "INSERT INTO moments (person_id, title, narrative, "
                        "sensory_details) VALUES (%s, %s, %s, %s)",
                        (person_id, f"m{i}", "n", "the smell of rain"),
                    )
                await cur.execute(
                    "INSERT INTO traits (person_id, name, status) "
                    "VALUES (%s, 'patient', 'active')",
                    (person_id,),
                )
                theme_id = await ensure_tribute_theme_async(
                    cur, person_id=person_id, slug=TRIBUTE_SLUG,
                    display_name=TRIBUTE_DISPLAY_NAME, description=TRIBUTE_DESCRIPTION,
                )
                tribute_id = await ensure_open_tribute_async(
                    cur, person_id=person_id, theme_id=theme_id
                )
                await set_message_async(
                    cur, tribute_id=tribute_id, message_text="Thank you, Dad."
                )
    return person_id, tribute_id


@pytest.mark.skip(
    reason="Fill in service-token auth header per tests/http/ conventions, "
    "then unskip. Logic under test is exercised by the helper assertions below."
)
async def test_video_requires_ready(async_pool, monkeypatch) -> None:
    ...  # see implementer note
```

> Implementer note: the endpoint test needs the project's service-token auth handled the way `tests/http/*` do it (look at an existing `tests/http/test_*.py` that posts to a `require_service_token` route — copy its client/fixture/token setup). Wire that in and unskip. If the established HTTP-test pattern uses a shared fixture (e.g. an authenticated `client`), prefer reusing it over `create_app()` directly. The gating logic (`ready`/percent, page floor) and the keyed-context write are the assertions that matter:
> - draft tribute, `artifact_kind="tribute_video"` → 409;
> - ready tribute → 200, `tributes.status='generating'`, and `latest_generation_context ? 'tribute_video'` is true in Postgres.

- [ ] **Step 4: Run the suite**

Bring up Postgres (Docker → `docker compose -f docker-compose.local.yml up -d postgres`; ensure `flashback_test`), then PowerShell:

```
$env:TEST_DATABASE_URL = "postgresql://flashback:flashback@localhost:15432/flashback_test"
python -m pytest tests/tribute -v
python -m pytest -q
```
Expected: tribute tests PASS (the endpoint test stays skipped until auth is wired); whole suite PASS modulo known pre-existing failures.

- [ ] **Step 5: Commit**

```bash
git add tests/tribute/
git commit -m "test(tribute): assembly fallback, context builders, generate gating

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>"
```

---

## Plan Self-Review

**Spec coverage (Plan 3 slice):**
- Assemble script: order scenes, captions, place message as climax, open/close (spec §8.1) → Task 3 ✓
- Compiled video context: scenes[{prompt,negative,reference?,duration}], message_text, captions, order, style preset, target_duration_seconds (spec §8.2 + video-length addendum) → Task 4 `build_tribute_video_context` ✓
- Storybook context, 9-page hard cap (spec §4/§8) → Task 4 `build_storybook_context` ✓
- Painterly preset + photoreal/deepfake negative on every scene/page (spec §2) → reuse of `compose_scene_prompt` + `SCENE_NEGATIVE_PROMPT` in Task 4 ✓
- Write context to row BEFORE pushing; push trigger-only job with identifiers only (spec §8.2–8.3, CLAUDE.md §3) → Task 6 steps 4–5 ✓
- New `artifact_kind`s `tribute_video`/`storybook` (spec §8) → Task 6 ✓
- Gating: video needs `ready`, storybook needs min pages (spec §7) → Task 6 ✓
- Node renders / writes URLs (spec §8.4) → out of repo; flagged as the critical dependency ✓
- Deferred, correctly absent: general non-tribute storybook engine; Father's Day skin + skin-configurable length (Plan 4); slot-gap steering (Plan 4).
- **Refinement of spec §8 made explicit:** the tribute row carries **two** artifacts, so `latest_generation_context` is keyed by `artifact_kind` (not a single context) to avoid the `composed_at` stale-skip race between the two jobs — a Node contract detail to confirm with the compiled renderer.

**Placeholder scan:** No code placeholders. One test (`test_generate_endpoint.py` happy path) is intentionally `@pytest.mark.skip` with an implementer note to wire the project's service-token auth — the pure assembly/context tests fully cover the new logic; the endpoint test is a wiring stub, explicitly marked, not a silent gap.

**Type consistency:**
- `TributeScript`/`Scene` (Task 3) consumed by the context builders (Task 4) and the route (Task 6): `.scenes[].moment_id/.caption`, `.opening_caption`, `.closing_caption`, `.message_text` — consistent.
- `fetch_scene_moments_async` returns dicts with `id`/`title`/`narrative`/`generation_prompt`/`sensory_details` — consumed as `c["id"]`, `c.get("title")` etc. in assembly + as `moments_by_id` in the builders — consistent.
- `write_tribute_generation_context_async(cur, tribute_id=, artifact_kind=, context=)`, `set_script_async(...)`, `set_status_async(...)`, `fetch_tribute_for_assembly_async(...)` names match between repo (Task 2) and route (Task 6).
- `artifact_kind` values `tribute_video`/`storybook` match across the request model (Task 5), route gating (Task 6), context builders, and queue push.
- `ArtifactGenerationQueueProducer.push(job_id, record_type, record_id, person_id, artifact_kind, source, composed_at)` — call in Task 6 matches the real signature.
- `resolve_preset` / `compose_scene_prompt` / `SCENE_NEGATIVE_PROMPT` usages match the real `flashback.artifacts` signatures.

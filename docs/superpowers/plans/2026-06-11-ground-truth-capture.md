# Ground-Truth Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture stable subject ground truth (region, era, attire, physical features) plus per-story time anchors via contextual in-chat tap cards, extraction inference, and onboarding — stored in `persons.ground_truth` JSONB and injected into artifact prompts, extraction prompts, and the response generator.

**Architecture:** A new `flashback/ground_truth/` package owns the field registry, the JSONB store (precedence-aware upserts), the audience-specific render helper, and the tap-selection LLM call. A new orchestrator step `select_ground_truth_tap` fires on `story`/`deepen` turns; answers travel back as a structured sidecar on `/turn` (never mined from text). Segment-anchor answers ride the extraction queue payload into the worker, which also emits `ground_truth_observations` from its existing big call.

**Tech Stack:** Python 3.11, FastAPI, pydantic v2, psycopg (async pool HTTP-side, sync worker-side), Valkey (redis-py asyncio), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-06-11-ground-truth-capture-design.md`

**Two deliberate deviations from the spec (amend the spec in Task 14):**
1. The migration is **0026**, not 0024 (0024/0025 already exist).
2. `region` is stored as a **free string** ("Karimnagar, Telangana, India"), not `{country, locale}`, and there is **no free-text normalizer LLM call** — prompts consume the text directly, so the structured shape and the normalizer bought nothing (YAGNI).

**Conventions for every task:** run tests with `python -m pytest <path> -v` from the repo root. Commit messages use the repo style (`feat(ground-truth): ...`) and end with `Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>` (never any other trailer). All new test dirs need an empty `__init__.py` (existing pattern: `tests/workers/extraction/__init__.py`).

---

### Task 1: Migration 0026 — `persons.ground_truth`

**Files:**
- Create: `migrations/0026_persons_ground_truth.up.sql`
- Create: `migrations/0026_persons_ground_truth.down.sql`

- [ ] **Step 1: Write the up migration**

```sql
-- migrations/0026_persons_ground_truth.up.sql
-- Ground-truth layer (design 2026-06-11): machine-consumable stable
-- subject facts. One key per registry field; each value is
--   {"value": ..., "provenance": "onboarding|inferred|tap|user_edit",
--    "confidence": "low|medium|high", "updated_at": "<ISO-8601>"}
-- The field registry lives in code (flashback/ground_truth/registry.py).
ALTER TABLE persons
    ADD COLUMN IF NOT EXISTS ground_truth JSONB NOT NULL DEFAULT '{}'::jsonb;
```

- [ ] **Step 2: Write the down migration**

```sql
-- migrations/0026_persons_ground_truth.down.sql
ALTER TABLE persons DROP COLUMN IF EXISTS ground_truth;
```

(Check `migrations/` for an existing `.down.sql` convention first — if the repo has no down files, skip this file.)

- [ ] **Step 3: Apply to the local dev database**

Run the same command used for prior migrations (check `README.md` / `local/` for the migration runner; if migrations are applied with plain psql, run `psql "$DATABASE_URL" -f migrations/0026_persons_ground_truth.up.sql`).
Expected: `ALTER TABLE`.

- [ ] **Step 4: Commit**

```bash
git add migrations/0026_persons_ground_truth.up.sql migrations/0026_persons_ground_truth.down.sql
git commit -m "feat(ground-truth): add persons.ground_truth JSONB (migration 0026)"
```

---

### Task 2: Field registry

**Files:**
- Create: `src/flashback/ground_truth/__init__.py`
- Create: `src/flashback/ground_truth/registry.py`
- Create: `tests/ground_truth/__init__.py`
- Test: `tests/ground_truth/test_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ground_truth/test_registry.py
from flashback.ground_truth.registry import (
    ASKABLE_KEYS,
    INFERRABLE_KEYS,
    REGISTRY,
    REGISTRY_BY_KEY,
)


def test_registry_has_all_nine_fields_in_priority_order():
    assert [f.key for f in REGISTRY] == [
        "region",
        "birth_era",
        "setting_type",
        "attire",
        "distinctive_features",
        "build",
        "cultural_context",
        "era_span",
        "languages",
    ]


def test_askable_excludes_inferred_only_and_derived_fields():
    assert "cultural_context" not in ASKABLE_KEYS
    assert "era_span" not in ASKABLE_KEYS
    assert "region" in ASKABLE_KEYS
    assert "languages" in ASKABLE_KEYS


def test_inferrable_excludes_only_era_span():
    assert "era_span" not in INFERRABLE_KEYS
    assert "cultural_context" in INFERRABLE_KEYS
    assert "region" in INFERRABLE_KEYS


def test_registry_by_key_roundtrip():
    assert REGISTRY_BY_KEY["attire"].value_type == "text"
    assert REGISTRY_BY_KEY["distinctive_features"].value_type == "list"
    assert REGISTRY_BY_KEY["languages"].value_type == "list"
    assert REGISTRY_BY_KEY["era_span"].value_type == "list"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ground_truth/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flashback.ground_truth'`

- [ ] **Step 3: Write the registry**

`src/flashback/ground_truth/__init__.py` is empty.

```python
# src/flashback/ground_truth/registry.py
"""Ground-truth field registry (design 2026-06-11, CLAUDE.md invariant #26).

Each field is a stable fact about the SUBJECT, stored under its key in
``persons.ground_truth``. ``askable`` fields may be asked via contextual
tap cards; inferred-only fields fill exclusively from extraction
observations; ``era_span`` is derived in code from moment time anchors.

Complexion / ethnicity is deliberately NOT a field — prompts derive it
from region + birth_era + cultural_context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ValueType = Literal["text", "list"]


@dataclass(frozen=True)
class GroundTruthField:
    key: str
    description: str  # shown to the selection + extraction LLMs
    askable: bool
    value_type: ValueType
    example_question: str  # seed phrasing; the selection LLM rephrases contextually


REGISTRY: tuple[GroundTruthField, ...] = (
    GroundTruthField(
        key="region",
        description=(
            "Where most of the subject's life happened — town/city, state, "
            "country (e.g. 'Karimnagar, Telangana, India')."
        ),
        askable=True,
        value_type="text",
        example_question="Where did most of their life happen?",
    ),
    GroundTruthField(
        key="birth_era",
        description=(
            "Decade the subject was born, approximately (e.g. '1950s'). "
            "Never a date of birth."
        ),
        askable=True,
        value_type="text",
        example_question="Roughly when were they born?",
    ),
    GroundTruthField(
        key="setting_type",
        description=(
            "The kind of place their life happened: village, small town, "
            "city, or farm."
        ),
        askable=True,
        value_type="text",
        example_question="What kind of place was that?",
    ),
    GroundTruthField(
        key="attire",
        description=(
            "What the subject usually wore (e.g. 'cotton saree', "
            "'shirt and lungi', 'always in a suit')."
        ),
        askable=True,
        value_type="text",
        example_question="What did they usually wear?",
    ),
    GroundTruthField(
        key="distinctive_features",
        description=(
            "Always-there physical details: glasses, mustache, braided "
            "hair, a walking stick."
        ),
        askable=True,
        value_type="list",
        example_question="When you picture them, is anything always there?",
    ),
    GroundTruthField(
        key="build",
        description=(
            "Overall physical impression: tall, slight, heavyset, wiry."
        ),
        askable=True,
        value_type="text",
        example_question="How would you picture them standing in a room?",
    ),
    GroundTruthField(
        key="cultural_context",
        description=(
            "Cultural / community background as it naturally surfaced "
            "(e.g. 'Telugu Hindu family'). Inferred only — NEVER asked."
        ),
        askable=False,
        value_type="text",
        example_question="",
    ),
    GroundTruthField(
        key="era_span",
        description=(
            "Decades the recalled memories span (e.g. ['1960s','1970s']). "
            "Derived from moment time anchors — never asked or LLM-emitted."
        ),
        askable=False,
        value_type="list",
        example_question="",
    ),
    GroundTruthField(
        key="languages",
        description="Languages the subject spoke at home / daily.",
        askable=True,
        value_type="list",
        example_question="Which language was home for them?",
    ),
)

REGISTRY_BY_KEY: dict[str, GroundTruthField] = {f.key: f for f in REGISTRY}
ASKABLE_KEYS: tuple[str, ...] = tuple(f.key for f in REGISTRY if f.askable)
# Everything the extraction LLM may observe. era_span is code-derived only.
INFERRABLE_KEYS: tuple[str, ...] = tuple(
    f.key for f in REGISTRY if f.key != "era_span"
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ground_truth/test_registry.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/flashback/ground_truth/ tests/ground_truth/
git commit -m "feat(ground-truth): field registry (9 fields, askable/inferrable split)"
```

---

### Task 3: Store — precedence-aware upserts

**Files:**
- Create: `src/flashback/ground_truth/store.py`
- Test: `tests/ground_truth/test_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ground_truth/test_store.py
from datetime import datetime, timezone

from flashback.ground_truth.store import apply_field

NOW = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)


def test_writes_new_field():
    out = apply_field(
        {}, field="region", value="Karimnagar, Telangana, India",
        provenance="inferred", confidence="high", now=NOW,
    )
    assert out is not None
    assert out["region"]["value"] == "Karimnagar, Telangana, India"
    assert out["region"]["provenance"] == "inferred"
    assert out["region"]["updated_at"] == NOW.isoformat()


def test_inferred_below_high_confidence_is_dropped():
    assert apply_field(
        {}, field="region", value="India",
        provenance="inferred", confidence="medium", now=NOW,
    ) is None


def test_lower_provenance_never_overwrites_higher():
    current = apply_field(
        {}, field="region", value="Karimnagar",
        provenance="tap", confidence="high", now=NOW,
    )
    assert apply_field(
        current, field="region", value="Mumbai",
        provenance="inferred", confidence="high", now=NOW,
    ) is None
    assert apply_field(
        current, field="region", value="Mumbai",
        provenance="onboarding", confidence="high", now=NOW,
    ) is None


def test_equal_provenance_refines():
    current = apply_field(
        {}, field="region", value="India",
        provenance="inferred", confidence="high", now=NOW,
    )
    out = apply_field(
        current, field="region", value="Karimnagar, Telangana, India",
        provenance="inferred", confidence="high", now=NOW,
    )
    assert out["region"]["value"] == "Karimnagar, Telangana, India"


def test_higher_provenance_overwrites():
    current = apply_field(
        {}, field="region", value="India",
        provenance="inferred", confidence="high", now=NOW,
    )
    out = apply_field(
        current, field="region", value="Karimnagar",
        provenance="user_edit", confidence="high", now=NOW,
    )
    assert out["region"]["provenance"] == "user_edit"


def test_list_fields_merge_union():
    current = apply_field(
        {}, field="distinctive_features", value="glasses",
        provenance="tap", confidence="high", now=NOW,
    )
    out = apply_field(
        current, field="distinctive_features", value=["mustache", "glasses"],
        provenance="tap", confidence="high", now=NOW,
    )
    assert out["distinctive_features"]["value"] == ["glasses", "mustache"]


def test_unknown_field_and_empty_value_rejected():
    assert apply_field({}, field="favourite_color", value="red",
                       provenance="tap", confidence="high", now=NOW) is None
    assert apply_field({}, field="region", value="  ",
                       provenance="tap", confidence="high", now=NOW) is None


def test_input_dict_not_mutated():
    current = {"region": {"value": "India", "provenance": "tap",
                          "confidence": "high", "updated_at": NOW.isoformat()}}
    apply_field(current, field="region", value="Karimnagar",
                provenance="user_edit", confidence="high", now=NOW)
    assert current["region"]["value"] == "India"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ground_truth/test_store.py -v`
Expected: FAIL with `ImportError` (no `store` module)

- [ ] **Step 3: Write the store**

```python
# src/flashback/ground_truth/store.py
"""Reads and precedence-aware writes for ``persons.ground_truth``.

Precedence: ``user_edit > tap > onboarding > inferred``. A write at a
lower rank than the stored value is dropped; equal rank refines (so a
better inference replaces an earlier one). Inferred writes additionally
require ``confidence == "high"`` (invariant #6 — under-extract).

``apply_field`` is pure (returns a new dict or ``None`` on rejection) so
the rules are testable without a database. The async helpers serve the
HTTP/orchestrator side; the ``*_sync`` helpers serve the extraction
worker, which runs sync cursors inside its own transaction.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import structlog

from flashback.ground_truth.registry import REGISTRY_BY_KEY

log = structlog.get_logger("flashback.ground_truth")

PROVENANCE_RANK: dict[str, int] = {
    "inferred": 0,
    "onboarding": 1,
    "tap": 2,
    "user_edit": 3,
}

_SELECT_FOR_UPDATE = (
    "SELECT ground_truth FROM persons WHERE id = %s FOR UPDATE"
)
_SELECT = "SELECT ground_truth FROM persons WHERE id = %s"
_UPDATE = "UPDATE persons SET ground_truth = %s::jsonb WHERE id = %s"


def apply_field(
    ground_truth: dict[str, Any] | None,
    *,
    field: str,
    value: Any,
    provenance: str,
    confidence: str,
    now: datetime,
) -> dict[str, Any] | None:
    """Return a NEW ground_truth dict with the write applied, or None if
    the write is rejected (unknown field, empty value, low-confidence
    inference, or insufficient provenance)."""
    spec = REGISTRY_BY_KEY.get(field)
    if spec is None or provenance not in PROVENANCE_RANK:
        return None
    if provenance == "inferred" and confidence != "high":
        return None

    cleaned = _clean_value(value, spec.value_type)
    if cleaned is None:
        return None

    current = dict(ground_truth or {})
    existing = current.get(field)
    if isinstance(existing, dict):
        existing_rank = PROVENANCE_RANK.get(str(existing.get("provenance")), 0)
        if PROVENANCE_RANK[provenance] < existing_rank:
            return None
        if spec.value_type == "list":
            merged = [v for v in (existing.get("value") or []) if isinstance(v, str)]
            for item in cleaned:
                if item not in merged:
                    merged.append(item)
            cleaned = merged

    current[field] = {
        "value": cleaned,
        "provenance": provenance,
        "confidence": confidence,
        "updated_at": now.isoformat(),
    }
    return current


def _clean_value(value: Any, value_type: str) -> Any | None:
    if value_type == "list":
        items = [value] if isinstance(value, str) else value
        if not isinstance(items, list):
            return None
        cleaned = [v.strip() for v in items if isinstance(v, str) and v.strip()]
        return cleaned or None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


# --- async (HTTP / orchestrator side) --------------------------------------


async def fetch_ground_truth(db_pool, person_id: UUID | str) -> dict[str, Any]:
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_SELECT, (str(person_id),))
            row = await cur.fetchone()
    if row is None or not isinstance(row[0], dict):
        return {}
    return row[0]


async def upsert_ground_truth_field(
    cur,
    person_id: UUID | str,
    *,
    field: str,
    value: Any,
    provenance: str,
    confidence: str = "high",
) -> bool:
    """Apply one write inside the caller's transaction. Returns True if
    written. ``cur`` is an async psycopg cursor."""
    await cur.execute(_SELECT_FOR_UPDATE, (str(person_id),))
    row = await cur.fetchone()
    current = row[0] if row is not None and isinstance(row[0], dict) else {}
    updated = apply_field(
        current, field=field, value=value, provenance=provenance,
        confidence=confidence, now=datetime.now(timezone.utc),
    )
    if updated is None:
        log.info("ground_truth.write_rejected", field=field, provenance=provenance)
        return False
    await cur.execute(_UPDATE, (json.dumps(updated), str(person_id)))
    log.info("ground_truth.written", field=field, provenance=provenance)
    return True


# --- sync (extraction worker side) ------------------------------------------


def fetch_ground_truth_sync(cursor, person_id: str) -> dict[str, Any]:
    cursor.execute(_SELECT, (person_id,))
    row = cursor.fetchone()
    if row is None or not isinstance(row[0], dict):
        return {}
    return row[0]


def apply_observations_sync(
    cursor, person_id: str, observations: list
) -> int:
    """Persist extraction-emitted observations (provenance='inferred').
    Only high-confidence observations are written (apply_field enforces).
    Returns the number written. Runs inside the worker's transaction."""
    if not observations:
        return 0
    cursor.execute(_SELECT_FOR_UPDATE, (person_id,))
    row = cursor.fetchone()
    current = row[0] if row is not None and isinstance(row[0], dict) else {}
    written = 0
    now = datetime.now(timezone.utc)
    for obs in observations:
        updated = apply_field(
            current, field=obs.field, value=obs.value,
            provenance="inferred", confidence=obs.confidence, now=now,
        )
        if updated is not None:
            current = updated
            written += 1
    if written:
        cursor.execute(_UPDATE, (json.dumps(current), person_id))
    return written


def recompute_era_span_sync(cursor, person_id: str) -> None:
    """Derive era_span (sorted decade list) from active moments' time
    anchors. Code-derived — never asked, never LLM-emitted."""
    cursor.execute(
        """
        SELECT DISTINCT COALESCE(
                   time_anchor->>'decade',
                   ((((time_anchor->>'year')::int) / 10) * 10)::text || 's'
               )
          FROM moments
         WHERE person_id = %s
           AND status = 'active'
           AND (time_anchor->>'decade' IS NOT NULL
                OR time_anchor->>'year' IS NOT NULL)
        """,
        (person_id,),
    )
    decades = sorted({row[0] for row in cursor.fetchall() if row[0]})
    if not decades:
        return
    cursor.execute(_SELECT_FOR_UPDATE, (person_id,))
    row = cursor.fetchone()
    current = row[0] if row is not None and isinstance(row[0], dict) else {}
    current["era_span"] = {
        "value": decades,
        "provenance": "inferred",
        "confidence": "high",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    cursor.execute(_UPDATE, (json.dumps(current), person_id))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ground_truth/test_store.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/flashback/ground_truth/store.py tests/ground_truth/test_store.py
git commit -m "feat(ground-truth): precedence-aware store (apply_field + async/sync upserts)"
```

---

### Task 4: Render helper

**Files:**
- Create: `src/flashback/ground_truth/render.py`
- Test: `tests/ground_truth/test_render.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ground_truth/test_render.py
from flashback.ground_truth.render import render_ground_truth_block


def _gt(**kwargs):
    return {
        k: {"value": v, "provenance": "tap", "confidence": "high",
            "updated_at": "2026-06-11T12:00:00+00:00"}
        for k, v in kwargs.items()
    }


def test_empty_ground_truth_renders_empty_string():
    for audience in ("extraction", "portrait", "scene", "responder"):
        assert render_ground_truth_block({}, audience) == ""


def test_extraction_renders_line_per_known_field_only():
    out = render_ground_truth_block(
        _gt(region="Karimnagar, Telangana, India", birth_era="1950s"),
        "extraction",
    )
    assert "region: Karimnagar, Telangana, India" in out
    assert "birth_era: 1950s" in out
    assert "attire" not in out  # silent on unknowns — never "attire: unknown"


def test_portrait_renders_descriptor_fragments():
    out = render_ground_truth_block(
        _gt(
            region="Karimnagar, Telangana, India",
            birth_era="1950s",
            attire="cotton saree",
            distinctive_features=["glasses"],
            build="slight",
        ),
        "portrait",
    )
    assert "from Karimnagar, Telangana, India" in out
    assert "born in the 1950s" in out
    assert "typically wearing cotton saree" in out
    assert "glasses" in out
    assert "slight build" in out


def test_portrait_excludes_languages():
    out = render_ground_truth_block(_gt(languages=["Telugu"]), "portrait")
    assert out == ""


def test_scene_renders_single_setting_line():
    out = render_ground_truth_block(
        _gt(region="Karimnagar, Telangana, India",
            era_span=["1960s", "1970s"], setting_type="village"),
        "scene",
    )
    assert out.startswith("Setting context:")
    assert "Karimnagar" in out
    assert "1960s" in out
    assert "village" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ground_truth/test_render.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write the renderer**

```python
# src/flashback/ground_truth/render.py
"""Render persons.ground_truth for each consumer (one helper, many readers).

Renders only fields that exist — silent on unknowns, never
"region: unknown". Audiences:

* ``extraction`` / ``responder`` — line-per-field text for an XML block.
* ``portrait``  — comma-joinable descriptor fragments for the portrait
  prompt. Ethnicity is never stated; the image model derives it from
  region + era + cultural context (spec §1).
* ``scene``     — one short "Setting context: ..." line appended on
  scene compose/regenerate.
"""

from __future__ import annotations

from typing import Any, Literal

Audience = Literal["extraction", "portrait", "scene", "responder"]

_PORTRAIT_ORDER = (
    "region", "birth_era", "cultural_context", "attire",
    "distinctive_features", "build", "setting_type",
)
_TEXT_ORDER = (
    "region", "birth_era", "setting_type", "attire",
    "distinctive_features", "build", "cultural_context",
    "era_span", "languages",
)


def render_ground_truth_block(
    ground_truth: dict[str, Any] | None, audience: Audience
) -> str:
    values = _known_values(ground_truth)
    if not values:
        return ""
    if audience in ("extraction", "responder"):
        return "\n".join(
            f"{key}: {_as_text(values[key])}"
            for key in _TEXT_ORDER
            if key in values
        )
    if audience == "portrait":
        return ", ".join(_portrait_fragments(values))
    return _scene_line(values)


def _known_values(ground_truth: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, entry in (ground_truth or {}).items():
        if isinstance(entry, dict) and entry.get("value") not in (None, "", []):
            out[key] = entry["value"]
    return out


def _as_text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _portrait_fragments(values: dict[str, Any]) -> list[str]:
    fragments: list[str] = []
    for key in _PORTRAIT_ORDER:
        if key not in values:
            continue
        text = _as_text(values[key])
        if key == "region":
            fragments.append(f"from {text}")
        elif key == "birth_era":
            fragments.append(f"born in the {text}")
        elif key == "attire":
            fragments.append(f"typically wearing {text}")
        elif key == "build":
            fragments.append(f"{text} build")
        elif key == "setting_type":
            fragments.append(f"{text} background")
        else:  # cultural_context, distinctive_features
            fragments.append(text)
    return fragments


def _scene_line(values: dict[str, Any]) -> str:
    parts: list[str] = []
    if "region" in values:
        parts.append(_as_text(values["region"]))
    era = values.get("era_span") or values.get("birth_era")
    if era:
        parts.append(f"{_as_text(era)} era")
    if "setting_type" in values:
        parts.append(f"{_as_text(values['setting_type'])} setting")
    if "cultural_context" in values:
        parts.append(_as_text(values["cultural_context"]))
    if not parts:
        return ""
    return "Setting context: " + ", ".join(parts) + "."
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ground_truth/test_render.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/flashback/ground_truth/render.py tests/ground_truth/test_render.py
git commit -m "feat(ground-truth): audience-specific render helper"
```

---

### Task 5: Working Memory fields + client methods

**Files:**
- Modify: `src/flashback/working_memory/schema.py`
- Modify: `src/flashback/working_memory/client.py`
- Test: `tests/working_memory/test_ground_truth_fields.py`

- [ ] **Step 1: Write the failing test**

Look at `tests/working_memory/conftest.py` first — it provides the fakeredis/real-redis fixture the existing WM tests use; mirror whatever fixture name `tests/working_memory/test_recently_asked.py` uses (call it `wm` below).

```python
# tests/working_memory/test_ground_truth_fields.py
import json
from datetime import datetime, timezone

import pytest

from flashback.working_memory.schema import (
    WorkingMemoryState,
    parse_state_hash,
    serialise_state_for_init,
)


def _state(**overrides):
    base = dict(
        person_id="p1", role_id="r1",
        started_at=datetime(2026, 6, 11, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return WorkingMemoryState(**base)


def test_new_fields_default_and_roundtrip():
    state = _state()
    assert state.gt_taps_emitted_this_session == 0
    assert state.signal_pending_gt_tap == ""
    assert state.gt_declined_fields == []
    assert state.segment_anchor_question == ""
    assert state.segment_anchor_answer == ""

    raw = serialise_state_for_init(state)
    parsed = parse_state_hash(raw)
    assert parsed.gt_taps_emitted_this_session == 0
    assert parsed.gt_declined_fields == []


def test_parse_state_hash_decodes_gt_fields():
    raw = serialise_state_for_init(_state())
    raw["gt_taps_emitted_this_session"] = "1"
    raw["gt_declined_fields"] = json.dumps(["attire"])
    raw["signal_pending_gt_tap"] = json.dumps(
        {"kind": "ground_truth", "field": "region", "question_text": "Where?"}
    )
    parsed = parse_state_hash(raw)
    assert parsed.gt_taps_emitted_this_session == 1
    assert parsed.gt_declined_fields == ["attire"]
    assert json.loads(parsed.signal_pending_gt_tap)["field"] == "region"


@pytest.mark.asyncio
async def test_client_gt_tap_lifecycle(wm):
    session_id = "sess-gt-1"
    await wm.initialize(
        session_id=session_id, person_id="p1", role_id="r1",
        started_at=datetime(2026, 6, 11, tzinfo=timezone.utc),
    )
    payload = json.dumps(
        {"kind": "ground_truth", "field": "region", "question_text": "Where?"}
    )
    await wm.record_gt_tap_emitted(
        session_id=session_id, payload_json=payload, question_text="Where?"
    )
    state = await wm.get_state(session_id)
    assert state.gt_taps_emitted_this_session == 1
    assert state.signal_pending_gt_tap == payload
    assert state.signal_pending_tap_question == "Where?"
    assert state.user_turns_since_last_tap == 0

    await wm.add_gt_declined_field(session_id, "region")
    await wm.clear_pending_gt_tap(session_id)
    state = await wm.get_state(session_id)
    assert state.gt_declined_fields == ["region"]
    assert state.signal_pending_gt_tap == ""


@pytest.mark.asyncio
async def test_client_segment_anchor_lifecycle(wm):
    session_id = "sess-gt-2"
    await wm.initialize(
        session_id=session_id, person_id="p1", role_id="r1",
        started_at=datetime(2026, 6, 11, tzinfo=timezone.utc),
    )
    await wm.set_segment_anchor(
        session_id, question_text="About when was that?", answer="In the 1970s"
    )
    state = await wm.get_state(session_id)
    assert state.segment_anchor_question == "About when was that?"
    assert state.segment_anchor_answer == "In the 1970s"

    await wm.clear_segment_anchor(session_id)
    state = await wm.get_state(session_id)
    assert state.segment_anchor_answer == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/working_memory/test_ground_truth_fields.py -v`
Expected: FAIL (`ValidationError` / `AttributeError` — fields and methods missing)

- [ ] **Step 3: Add the schema fields**

In `src/flashback/working_memory/schema.py`, add to `WorkingMemoryState` after the `signal_pending_tap_question` field:

```python
    # ---- Ground-truth capture (design 2026-06-11) -----------------------
    # Count of GT taps emitted this session (cap = 1).
    gt_taps_emitted_this_session: int = 0
    # JSON payload of the pending GT tap: {"kind","field","question_text"}.
    # Empty when none pending. The /turn sidecar handler reads it to know
    # what the answer refers to, then clears it.
    signal_pending_gt_tap: str = ""
    # Fields the user skipped this session — never re-asked this session.
    gt_declined_fields: list[str] = Field(default_factory=list)
    # Pending segment time-anchor answer for the OPEN segment. Carried
    # into the extraction payload at boundary/wrap, then cleared.
    segment_anchor_question: str = ""
    segment_anchor_answer: str = ""
```

Add `"gt_taps_emitted_this_session"` to `_INT_FIELDS`. In `parse_state_hash`, extend the `emitted_tap_question_ids` branch:

```python
        elif key in ("emitted_tap_question_ids", "gt_declined_fields"):
            parsed[key] = json.loads(value) if value else []
```

In `serialise_state_for_init`, add to the returned mapping:

```python
        "gt_taps_emitted_this_session": str(state.gt_taps_emitted_this_session),
        "signal_pending_gt_tap": state.signal_pending_gt_tap,
        "gt_declined_fields": json.dumps(state.gt_declined_fields),
        "segment_anchor_question": state.segment_anchor_question,
        "segment_anchor_answer": state.segment_anchor_answer,
```

- [ ] **Step 4: Add the client methods**

In `src/flashback/working_memory/client.py`, add methods to `WorkingMemory`, mirroring the pipeline/expire style of the existing `record_tap_emitted` (read that method first and copy its idiom — e.g. if it uses `self._redis.pipeline()` + `hset` + `expire`, do the same):

```python
    async def record_gt_tap_emitted(
        self, *, session_id: str, payload_json: str, question_text: str
    ) -> None:
        """Mark a ground-truth tap as pending. Bumps the session GT cap
        counter, resets the shared tap cooldown so a coverage tap can't
        fire on the immediately-next turn, and seeds the classifier's
        pending-tap signal so a terse chip reply classifies as story."""
        key = state_key(session_id)
        pipe = self._redis.pipeline()
        pipe.hset(
            key,
            mapping={
                "signal_pending_gt_tap": payload_json,
                "signal_pending_tap_question": question_text,
                "user_turns_since_last_tap": "0",
            },
        )
        pipe.hincrby(key, "gt_taps_emitted_this_session", 1)
        pipe.expire(key, self._ttl)
        await pipe.execute()

    async def clear_pending_gt_tap(self, session_id: str) -> None:
        key = state_key(session_id)
        await self._redis.hset(key, "signal_pending_gt_tap", "")
        await self._redis.expire(key, self._ttl)

    async def add_gt_declined_field(self, session_id: str, field: str) -> None:
        key = state_key(session_id)
        raw = await self._redis.hget(key, "gt_declined_fields")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        declined = json.loads(raw) if raw else []
        if field not in declined:
            declined.append(field)
        await self._redis.hset(key, "gt_declined_fields", json.dumps(declined))
        await self._redis.expire(key, self._ttl)

    async def set_segment_anchor(
        self, session_id: str, *, question_text: str, answer: str
    ) -> None:
        key = state_key(session_id)
        await self._redis.hset(
            key,
            mapping={
                "segment_anchor_question": question_text,
                "segment_anchor_answer": answer,
            },
        )
        await self._redis.expire(key, self._ttl)

    async def clear_segment_anchor(self, session_id: str) -> None:
        key = state_key(session_id)
        await self._redis.hset(
            key,
            mapping={"segment_anchor_question": "", "segment_anchor_answer": ""},
        )
        await self._redis.expire(key, self._ttl)
```

- [ ] **Step 5: Run tests (new + full WM suite, schema serialisation tests live there)**

Run: `python -m pytest tests/working_memory/ -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/flashback/working_memory/ tests/working_memory/test_ground_truth_fields.py
git commit -m "feat(ground-truth): working-memory fields + client methods for GT taps and segment anchors"
```

---

### Task 6: Tap model gains `kind` + `field`; nullable `question_id`

**Files:**
- Modify: `src/flashback/orchestrator/protocol.py:15-28`
- Modify: `src/flashback/orchestrator/steps/append_response.py:23-26`
- Test: `tests/orchestrator/test_tap_model.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/orchestrator/test_tap_model.py
from uuid import uuid4

from flashback.orchestrator.protocol import Tap


def test_coverage_tap_unchanged_defaults():
    tap = Tap(question_id=uuid4(), text="Q?", dimension="sensory")
    assert tap.kind == "coverage"
    assert tap.field is None


def test_ground_truth_tap_allows_null_question_id():
    tap = Tap(
        question_id=None, text="Where did most of her life happen?",
        dimension="", kind="ground_truth", field="region",
        options=["Karimnagar", "Hyderabad", "Another state", "Outside India"],
    )
    dumped = tap.model_dump(mode="json")
    assert dumped["question_id"] is None
    assert dumped["kind"] == "ground_truth"
    assert dumped["field"] == "region"


def test_segment_anchor_tap():
    tap = Tap(question_id=None, text="About when was that?",
              dimension="", kind="segment_anchor")
    assert tap.field is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/orchestrator/test_tap_model.py -v`
Expected: FAIL (`ValidationError: extra inputs are not permitted` for `kind`)

- [ ] **Step 3: Extend the Tap model**

In `src/flashback/orchestrator/protocol.py`, replace the `Tap` class body fields:

```python
class Tap(BaseModel):
    """A tappable question chip surfaced beneath an agent reply.

    `options` are short tappable answer chips generated per-turn by a
    small LLM call. Empty list when generation failed or was skipped —
    the UI falls back to free-text input only.

    `kind` distinguishes the three tap surfaces: `coverage` (P0 bank,
    has a question row), `ground_truth` (registry field capture — no
    question row, `field` carries the registry key), and
    `segment_anchor` (time anchor for the live story). Ground-truth and
    anchor answers return as the structured `ground_truth_answer`
    sidecar on the next /turn, never as mined text.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: UUID | None
    text: str
    dimension: str
    options: list[str] = Field(default_factory=list)
    kind: Literal["coverage", "ground_truth", "segment_anchor"] = "coverage"
    field: str | None = None
```

Add `Literal` to the existing `typing` import at the top of the file (it is already imported — verify).

- [ ] **Step 4: Guard the assistant-turn metadata write**

In `src/flashback/orchestrator/steps/append_response.py`, change:

```python
        if state.taps:
            metadata["tap_question_ids"] = [
                str(tap.question_id) for tap in state.taps
            ]
```

to:

```python
        if state.taps:
            metadata["tap_question_ids"] = [
                str(tap.question_id)
                for tap in state.taps
                if tap.question_id is not None
            ]
```

- [ ] **Step 5: Run the orchestrator + http suites (Tap is on the wire)**

Run: `python -m pytest tests/orchestrator/ tests/http/ -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/flashback/orchestrator/protocol.py src/flashback/orchestrator/steps/append_response.py tests/orchestrator/test_tap_model.py
git commit -m "feat(ground-truth): Tap model gains kind/field, question_id nullable"
```

---

### Task 7: Selection LLM call + anchor chip derivation

**Files:**
- Create: `src/flashback/ground_truth/selection_llm.py`
- Test: `tests/ground_truth/test_selection_llm.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ground_truth/test_selection_llm.py
from flashback.ground_truth.selection_llm import derive_anchor_chips


def test_anchor_chips_derived_from_birth_era():
    chips = derive_anchor_chips("1950s")
    assert chips == [
        "When they were young",
        "In the 1970s",
        "In the 1980s",
        "Later in life",
    ]


def test_anchor_chips_fallback_without_birth_era():
    chips = derive_anchor_chips(None)
    assert len(chips) == 4
    assert "Not sure" in chips


def test_anchor_chips_fallback_on_unparseable_era():
    assert "Not sure" in derive_anchor_chips("a while ago")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ground_truth/test_selection_llm.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write the module**

```python
# src/flashback/ground_truth/selection_llm.py
"""The single small-LLM call behind contextual ground-truth taps.

Given the unknown askable fields, the rolling summary, and the most
recent turns, it returns one of: skip (nothing natural to ask, or the
answer is already evident in this conversation — the "Hyderabad rule"),
a person-field question, or a segment time-anchor question. Best-effort:
any failure returns None and the turn proceeds untouched (mirrors
``tap_options``).
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from flashback.ground_truth.registry import GroundTruthField
from flashback.llm.errors import LLMError
from flashback.llm.interface import call_with_tool
from flashback.llm.prompt_safety import xml_text
from flashback.llm.tool_spec import ToolSpec

log = structlog.get_logger("flashback.ground_truth.selection")

_SELECT_GT_SYSTEM = """\
You decide whether to surface ONE small tap-card question beneath the
agent's reply, while a contributor is mid-story about a loved one. The
card captures stable ground truth about the SUBJECT (where their life
happened, what they wore, what they looked like) or anchors the story
being told in time. The user taps one chip — it must never feel like a
form or a survey.

You are given: the fields still unknown, what is already known, the
rolling summary of this session, and the most recent turns.

Decision rules, in order:
1. If the answer to a candidate field is ALREADY evident anywhere in
   this conversation (rolling summary or recent turns), do NOT ask it —
   it will be inferred. (E.g. they said "Hyderabad": region is evident.)
2. Only ask a field the CURRENT story naturally touches. A question
   about clothing during a story set at home is natural; a question
   about languages during a story about a road trip is not. If no
   unknown field is touched by the live story, skip.
3. If anchor is allowed and the story being told has NO time signal at
   all (no year, age, decade, or life-period mention), an anchor
   question ("About when was that?") may be the most natural ask.
4. When in doubt, skip. Skipping is free; a misplaced question is not.

Question style: one short sentence of fond curiosity about the story
("Where was that house?", "What would she have been wearing back
then?"). NEVER clinical ("What is her skin tone?" is banned — never ask
about complexion, ethnicity, or race). Options follow the chip rules:
exactly 4, each 2-6 words, concrete memory-fragment register, no
taxonomic buckets, no proper nouns you weren't given.

Call the `select_ground_truth_question` tool exactly once.
"""

_SELECT_GT_TOOL = ToolSpec(
    name="select_ground_truth_question",
    description="Choose skip, one person-field question, or one anchor question.",
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["skip", "ask_field", "ask_anchor"],
            },
            "field": {
                "type": "string",
                "description": "Registry key. Required when action=ask_field.",
            },
            "question_text": {"type": "string", "maxLength": 200},
            "options": {
                "type": "array",
                "minItems": 4,
                "maxItems": 4,
                "items": {"type": "string", "minLength": 1, "maxLength": 60},
            },
            "reason": {"type": "string", "maxLength": 200},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
)


async def select_ground_truth_question(
    *,
    settings,
    person_name: str,
    person_relationship: str | None,
    unknown_fields: list[GroundTruthField],
    known_block: str,
    rolling_summary: str,
    recent_turns: list[tuple[str, str]],
    anchor_allowed: bool,
) -> dict[str, Any] | None:
    """Returns the validated tool args dict, or None on skip/failure."""
    if settings is None or (not unknown_fields and not anchor_allowed):
        return None

    field_lines = "\n".join(
        f"- {f.key}: {f.description} (e.g. \"{f.example_question}\")"
        for f in unknown_fields
    )
    turn_lines = "\n".join(
        f"{role}: {xml_text(content)}" for role, content in recent_turns
    )
    rel_attr = (
        f' relationship="{xml_text(person_relationship)}"'
        if person_relationship
        else ""
    )
    user_block = "\n".join(
        [
            f"<subject{rel_attr}>{xml_text(person_name)}</subject>",
            f"<anchor_allowed>{str(anchor_allowed).lower()}</anchor_allowed>",
            "<unknown_fields>",
            field_lines or "(none)",
            "</unknown_fields>",
            "<known_ground_truth>",
            xml_text(known_block) or "(nothing known yet)",
            "</known_ground_truth>",
            "<rolling_summary>",
            xml_text(rolling_summary or ""),
            "</rolling_summary>",
            "<recent_turns>",
            turn_lines,
            "</recent_turns>",
        ]
    )

    try:
        args = await call_with_tool(
            provider=settings.llm_small_provider,
            model=settings.llm_intent_model,
            system_prompt=_SELECT_GT_SYSTEM,
            user_message=user_block,
            tool=_SELECT_GT_TOOL,
            max_tokens=300,
            timeout=10.0,
            settings=settings,
        )
    except LLMError as exc:
        log.warning("gt_selection.llm_failed", error=str(exc))
        return None
    except Exception as exc:  # defensive — never block a turn on tap selection
        log.warning(
            "gt_selection.unexpected_failure",
            error_type=type(exc).__name__,
            detail=str(exc),
        )
        return None

    if not isinstance(args, dict) or args.get("action") == "skip":
        return None
    action = args.get("action")
    if action == "ask_field":
        valid_keys = {f.key for f in unknown_fields}
        if args.get("field") not in valid_keys or not args.get("question_text"):
            return None
    elif action == "ask_anchor":
        if not anchor_allowed or not args.get("question_text"):
            return None
    else:
        return None
    return args


def derive_anchor_chips(birth_era: str | None) -> list[str]:
    """Deterministic anchor chips. With birth_era known, chips map to
    concrete decades; otherwise era-neutral fallbacks."""
    match = re.match(r"^\s*(\d{4})", birth_era or "")
    if match:
        start = int(match.group(1))
        return [
            "When they were young",
            f"In the {start + 20}s",
            f"In the {start + 30}s",
            "Later in life",
        ]
    return [
        "Before I was born",
        "When I was a kid",
        "More recently",
        "Not sure",
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ground_truth/test_selection_llm.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/flashback/ground_truth/selection_llm.py tests/ground_truth/test_selection_llm.py
git commit -m "feat(ground-truth): GT tap selection LLM call + derived anchor chips"
```

---

### Task 8: Orchestrator step `select_ground_truth_tap` + pipeline wiring

**Files:**
- Create: `src/flashback/orchestrator/steps/select_ground_truth_tap.py`
- Modify: `src/flashback/orchestrator/steps/__init__.py` (export the step — mirror how `select_coverage_tap` is exported)
- Modify: `src/flashback/orchestrator/orchestrator.py` (`handle_turn` ~line 337, `handle_turn_stream` ~line 466)
- Modify: `src/flashback/orchestrator/failure_policy.py:28-38`
- Modify: `src/flashback/orchestrator/steps/generate_response.py` (`build_turn_context`, tap_pending logic)
- Test: `tests/orchestrator/test_select_ground_truth_tap.py`

- [ ] **Step 1: Write the failing test**

Mirror the mocking style of existing orchestrator step tests (see `tests/orchestrator/test_stub_with_intent.py` for how deps are faked). Use `unittest.mock.AsyncMock` for `deps.working_memory` and monkeypatch the module-level LLM + DB helpers:

```python
# tests/orchestrator/test_select_ground_truth_tap.py
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import flashback.orchestrator.steps.select_ground_truth_tap as step_mod
from flashback.intent_classifier.schema import IntentResult
from flashback.orchestrator.state import TurnState
from flashback.working_memory.schema import Turn, WorkingMemoryState


def _turn(role, content):
    return Turn(role=role, content=content,
                timestamp=datetime(2026, 6, 11, tzinfo=timezone.utc))


def _state(intent="story", temperature="medium", user_turns=4):
    state = TurnState(
        turn_id=uuid4(), session_id=uuid4(), person_id=uuid4(),
        role_id=uuid4(), user_message="...",
        started_at=datetime(2026, 6, 11, tzinfo=timezone.utc),
    )
    state.intent_result = IntentResult(
        intent=intent, confidence=0.9, emotional_temperature=temperature
    )
    state.effective_temperature = temperature
    state.transcript = [_turn("user", f"m{i}") for i in range(user_turns)]
    return state


def _wm_state(**overrides):
    base = dict(
        person_id="p", role_id="r",
        started_at=datetime(2026, 6, 11, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return WorkingMemoryState(**base)


def _deps(wm_state):
    wm = AsyncMock()
    wm.get_state.return_value = wm_state
    wm.get_transcript.return_value = []
    return SimpleNamespace(
        working_memory=wm, db_pool=object(), settings=SimpleNamespace()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["switch", "clarify", "recall"])
async def test_skips_on_non_story_intents(intent, monkeypatch):
    called = AsyncMock()
    monkeypatch.setattr(step_mod, "fetch_ground_truth", called)
    state = _state(intent=intent)
    await step_mod.select_ground_truth_tap(state, _deps(_wm_state()))
    assert state.taps == []
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_on_high_temperature(monkeypatch):
    called = AsyncMock()
    monkeypatch.setattr(step_mod, "fetch_ground_truth", called)
    state = _state(temperature="high")
    await step_mod.select_ground_truth_tap(state, _deps(_wm_state()))
    assert state.taps == []
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_session_cap_reached(monkeypatch):
    called = AsyncMock()
    monkeypatch.setattr(step_mod, "fetch_ground_truth", called)
    state = _state()
    deps = _deps(_wm_state(gt_taps_emitted_this_session=1))
    await step_mod.select_ground_truth_tap(state, deps)
    assert state.taps == []
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_before_three_user_turns(monkeypatch):
    called = AsyncMock()
    monkeypatch.setattr(step_mod, "fetch_ground_truth", called)
    state = _state(user_turns=2)
    await step_mod.select_ground_truth_tap(state, _deps(_wm_state()))
    assert state.taps == []
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_another_tap_pending(monkeypatch):
    from flashback.orchestrator.protocol import Tap
    state = _state()
    state.taps = [Tap(question_id=uuid4(), text="q", dimension="sensory")]
    await step_mod.select_ground_truth_tap(state, _deps(_wm_state()))
    assert len(state.taps) == 1  # untouched


@pytest.mark.asyncio
async def test_emits_field_tap_and_records_pending(monkeypatch):
    monkeypatch.setattr(
        step_mod, "fetch_ground_truth", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        step_mod,
        "select_ground_truth_question",
        AsyncMock(return_value={
            "action": "ask_field", "field": "region",
            "question_text": "Where was that house?",
            "options": ["Karimnagar", "Hyderabad", "Another town", "Abroad"],
        }),
    )
    state = _state()
    deps = _deps(_wm_state())
    await step_mod.select_ground_truth_tap(state, deps)
    assert len(state.taps) == 1
    tap = state.taps[0]
    assert tap.kind == "ground_truth"
    assert tap.field == "region"
    assert tap.question_id is None
    deps.working_memory.record_gt_tap_emitted.assert_awaited_once()


@pytest.mark.asyncio
async def test_anchor_tap_uses_derived_chips_when_birth_era_known(monkeypatch):
    gt = {"birth_era": {"value": "1950s", "provenance": "tap",
                        "confidence": "high", "updated_at": "x"}}
    monkeypatch.setattr(
        step_mod, "fetch_ground_truth", AsyncMock(return_value=gt)
    )
    monkeypatch.setattr(
        step_mod,
        "select_ground_truth_question",
        AsyncMock(return_value={
            "action": "ask_anchor",
            "question_text": "About when was that?",
            "options": ["a", "b", "c", "d"],
        }),
    )
    state = _state()
    await step_mod.select_ground_truth_tap(state, _deps(_wm_state()))
    assert state.taps[0].kind == "segment_anchor"
    assert state.taps[0].options == [
        "When they were young", "In the 1970s", "In the 1980s", "Later in life",
    ]


@pytest.mark.asyncio
async def test_llm_skip_means_no_tap(monkeypatch):
    monkeypatch.setattr(
        step_mod, "fetch_ground_truth", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        step_mod, "select_ground_truth_question", AsyncMock(return_value=None)
    )
    state = _state()
    deps = _deps(_wm_state())
    await step_mod.select_ground_truth_tap(state, deps)
    assert state.taps == []
    deps.working_memory.record_gt_tap_emitted.assert_not_awaited()
```

(`IntentResult` constructor fields: check `src/flashback/intent_classifier/schema.py` and adjust the test construction to its exact signature before running.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/orchestrator/test_select_ground_truth_tap.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the step**

```python
# src/flashback/orchestrator/steps/select_ground_truth_tap.py
"""Contextual ground-truth tap selection for story / deepen turns.

A strict extension of the turn pipeline (design 2026-06-11): it only
ever ATTACHES a tap to the reply. It never alters intent handling,
retrieval, or steady question selection, and it never fires on switch
(that surface belongs to the question bank).
"""

from __future__ import annotations

import json

import structlog

from flashback.ground_truth.registry import REGISTRY
from flashback.ground_truth.render import render_ground_truth_block
from flashback.ground_truth.selection_llm import (
    derive_anchor_chips,
    select_ground_truth_question,
)
from flashback.ground_truth.store import fetch_ground_truth
from flashback.orchestrator.deps import OrchestratorDeps
from flashback.orchestrator.instrumentation import timed_step
from flashback.orchestrator.protocol import Tap
from flashback.orchestrator.state import TurnState

log = structlog.get_logger("flashback.orchestrator")

GT_TAPS_PER_SESSION_CAP = 1
MIN_USER_TURNS_BEFORE_GT_TAP = 3


async def select_ground_truth_tap(state: TurnState, deps: OrchestratorDeps) -> None:
    """Emit at most one ground-truth / segment-anchor tap per session."""

    with timed_step(log, "select_ground_truth_tap"):
        if state.intent_result is None or state.intent_result.intent not in {
            "story",
            "deepen",
        }:
            return
        if state.effective_temperature == "high":
            log.info("gt_tap.skipped", reason="high_temperature")
            return
        if state.taps:
            log.info("gt_tap.skipped", reason="other_tap_pending")
            return

        wm_state = state.working_memory_state or await deps.working_memory.get_state(
            str(state.session_id)
        )
        state.working_memory_state = wm_state
        if wm_state.gt_taps_emitted_this_session >= GT_TAPS_PER_SESSION_CAP:
            log.info("gt_tap.skipped", reason="session_cap")
            return

        transcript = state.transcript or await deps.working_memory.get_transcript(
            str(state.session_id)
        )
        state.transcript = transcript
        user_turn_count = sum(1 for turn in transcript if turn.role == "user")
        if user_turn_count < MIN_USER_TURNS_BEFORE_GT_TAP:
            log.info("gt_tap.skipped", reason="too_early",
                     user_turns=user_turn_count)
            return

        ground_truth = await fetch_ground_truth(deps.db_pool, state.person_id)
        declined = set(wm_state.gt_declined_fields)
        unknown_fields = [
            f for f in REGISTRY
            if f.askable and f.key not in ground_truth and f.key not in declined
        ]
        anchor_allowed = not wm_state.segment_anchor_answer
        if not unknown_fields and not anchor_allowed:
            log.info("gt_tap.skipped", reason="nothing_to_ask")
            return

        recent = [(t.role, t.content) for t in transcript[-12:]]
        result = await select_ground_truth_question(
            settings=deps.settings,
            person_name=state.person_name,
            person_relationship=state.person_relationship,
            unknown_fields=unknown_fields,
            known_block=render_ground_truth_block(ground_truth, "responder"),
            rolling_summary=wm_state.rolling_summary or "",
            recent_turns=recent,
            anchor_allowed=anchor_allowed,
        )
        if result is None:
            log.info("gt_tap.skipped", reason="llm_skip_or_failure")
            return

        if result["action"] == "ask_anchor":
            kind, field = "segment_anchor", None
            birth_era_entry = ground_truth.get("birth_era") or {}
            options = (
                derive_anchor_chips(birth_era_entry.get("value"))
                if birth_era_entry.get("value")
                else [str(o) for o in (result.get("options") or [])][:4]
            )
        else:
            kind, field = "ground_truth", str(result["field"])
            options = [str(o) for o in (result.get("options") or [])][:4]

        question_text = str(result["question_text"]).strip()
        tap = Tap(
            question_id=None,
            text=question_text,
            dimension="",
            options=options,
            kind=kind,
            field=field,
        )
        state.taps = [tap]
        await deps.working_memory.record_gt_tap_emitted(
            session_id=str(state.session_id),
            payload_json=json.dumps(
                {"kind": kind, "field": field, "question_text": question_text}
            ),
            question_text=question_text,
        )
        log.info("gt_tap.selected", kind=kind, field=field)
```

- [ ] **Step 4: Wire into the pipeline**

1. Export from `src/flashback/orchestrator/steps/__init__.py` (add `select_ground_truth_tap` alongside `select_coverage_tap` — match the existing import/`__all__` style).
2. In `src/flashback/orchestrator/failure_policy.py`, add to `TURN_POLICIES` after `"retrieve"`:

```python
    "select_ground_truth_tap": Policy.DEGRADE,
```

3. In `src/flashback/orchestrator/orchestrator.py`, add to the imports from `flashback.orchestrator.steps`: `select_ground_truth_tap`. Then in **both** `handle_turn` (after the `retrieve` if-block, before the `switch` if-block) and `handle_turn_stream` (same position), insert:

```python
            if state.effective_intent in {"story", "deepen"}:
                await execute(
                    policies=TURN_POLICIES,
                    step_name="select_ground_truth_tap",
                    fn=lambda: select_ground_truth_tap(state, self._deps),
                    state=state,
                )
```

- [ ] **Step 5: GT taps must NOT trigger the acknowledgment-only branch**

In `src/flashback/orchestrator/steps/generate_response.py` `build_turn_context`, the tap fields currently key off any tap. Change:

```python
        tap_pending=bool(state.taps),
        tap_question_text=(state.taps[0].text if state.taps else None),
        tap_dimension=(
            state.taps[0].dimension
            if state.taps and state.taps[0].dimension
            else None
        ),
```

to:

```python
        # Only coverage/promoted taps switch the prompt to acknowledgment-
        # only mode. A ground-truth / anchor tap is a side-capture riding
        # beneath a normal engaged reply (design 2026-06-11 §3b).
        tap_pending=any(t.kind == "coverage" for t in state.taps),
        tap_question_text=next(
            (t.text for t in state.taps if t.kind == "coverage"), None
        ),
        tap_dimension=next(
            (t.dimension for t in state.taps if t.kind == "coverage" and t.dimension),
            None,
        ),
```

- [ ] **Step 6: Run the new test + full orchestrator suite**

Run: `python -m pytest tests/orchestrator/ -v`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/flashback/orchestrator/ tests/orchestrator/test_select_ground_truth_tap.py
git commit -m "feat(ground-truth): contextual select_ground_truth_tap step on story/deepen turns"
```

---

### Task 9: `/turn` sidecar — `ground_truth_answer`

**Files:**
- Modify: `src/flashback/http/models.py` (add `GroundTruthAnswerInput`, extend `TurnRequest`)
- Create: `src/flashback/http/ground_truth_answer.py` (shared handler for `/turn` + `/turn/stream`)
- Modify: `src/flashback/http/routes/turn.py:69-91`
- Modify: `src/flashback/http/routes/stream.py` (mirror — find the `question_decision` handling block there and add the same call)
- Test: `tests/http/test_ground_truth_answer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/http/test_ground_truth_answer.py
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from flashback.http.ground_truth_answer import persist_ground_truth_answer
from flashback.http.models import GroundTruthAnswerInput


def _pending(kind="ground_truth", field="region", question="Where?"):
    return json.dumps({"kind": kind, "field": field, "question_text": question})


def _wm(pending_json):
    wm = AsyncMock()
    wm.get_state.return_value = SimpleNamespace(
        signal_pending_gt_tap=pending_json
    )
    return wm


@pytest.mark.asyncio
async def test_no_pending_tap_ignores_answer():
    wm = _wm("")
    answer = GroundTruthAnswerInput(kind="ground_truth", field="region",
                                    option_label="Karimnagar")
    await persist_ground_truth_answer(
        session_id=uuid4(), person_id=uuid4(), answer=answer,
        wm=wm, db_pool=None,
    )
    wm.clear_pending_gt_tap.assert_not_awaited()


@pytest.mark.asyncio
async def test_skipped_marks_declined_and_clears():
    wm = _wm(_pending())
    answer = GroundTruthAnswerInput(kind="ground_truth", field="region",
                                    skipped=True)
    await persist_ground_truth_answer(
        session_id=uuid4(), person_id=uuid4(), answer=answer,
        wm=wm, db_pool=None,
    )
    wm.add_gt_declined_field.assert_awaited_once()
    wm.clear_pending_gt_tap.assert_awaited_once()


@pytest.mark.asyncio
async def test_segment_anchor_answer_goes_to_working_memory():
    wm = _wm(_pending(kind="segment_anchor", field=None,
                      question="About when was that?"))
    answer = GroundTruthAnswerInput(kind="segment_anchor",
                                    option_label="In the 1970s")
    await persist_ground_truth_answer(
        session_id=uuid4(), person_id=uuid4(), answer=answer,
        wm=wm, db_pool=None,
    )
    wm.set_segment_anchor.assert_awaited_once()
    kwargs = wm.set_segment_anchor.await_args.kwargs
    assert kwargs["answer"] == "In the 1970s"
    wm.clear_pending_gt_tap.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/http/test_ground_truth_answer.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Add the request model**

In `src/flashback/http/models.py`, after `QuestionDecisionInput`:

```python
class GroundTruthAnswerInput(BaseModel):
    """Structured answer to a ground-truth / segment-anchor tap, carried
    on the next /turn. The conversation text never carries this Q&A —
    extraction never mines it (design 2026-06-11 §3c)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["ground_truth", "segment_anchor"]
    field: str | None = Field(default=None, max_length=64)
    option_label: str | None = Field(default=None, max_length=200)
    free_text: str | None = Field(default=None, max_length=500)
    skipped: bool = False
```

Extend `TurnRequest` with:

```python
    ground_truth_answer: GroundTruthAnswerInput | None = None
```

Find the request model used by `/turn/stream` (check `src/flashback/http/routes/stream.py` — it reuses `TurnRequest` or has a twin; if a twin, add the same field there).

- [ ] **Step 4: Write the shared handler**

```python
# src/flashback/http/ground_truth_answer.py
"""Persist a ground-truth tap answer before the turn pipeline runs.

Shared by /turn and /turn/stream. Idempotent against UI replays: an
answer arriving with no pending GT tap in Working Memory is ignored
(design 2026-06-11 §7)."""

from __future__ import annotations

import json
from uuid import UUID

import structlog

from flashback.ground_truth.store import upsert_ground_truth_field
from flashback.http.models import GroundTruthAnswerInput

log = structlog.get_logger("flashback.http.ground_truth")


async def persist_ground_truth_answer(
    *,
    session_id: UUID,
    person_id: UUID,
    answer: GroundTruthAnswerInput,
    wm,
    db_pool,
) -> None:
    state = await wm.get_state(str(session_id))
    raw_pending = state.signal_pending_gt_tap
    if not raw_pending:
        log.info("ground_truth_answer.ignored", reason="no_pending_tap")
        return
    pending = json.loads(raw_pending)

    value = (answer.option_label or answer.free_text or "").strip()

    if answer.skipped:
        if pending.get("kind") == "ground_truth" and pending.get("field"):
            await wm.add_gt_declined_field(str(session_id), pending["field"])
        log.info("ground_truth_answer.skipped", field=pending.get("field"))
    elif pending.get("kind") == "segment_anchor":
        if value:
            await wm.set_segment_anchor(
                str(session_id),
                question_text=pending.get("question_text", ""),
                answer=value,
            )
            log.info("ground_truth_answer.anchor_recorded")
    elif pending.get("kind") == "ground_truth" and pending.get("field") and value:
        async with db_pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await upsert_ground_truth_field(
                        cur,
                        person_id,
                        field=pending["field"],
                        value=value,
                        provenance="tap",
                        confidence="high",
                    )
        log.info("ground_truth_answer.recorded", field=pending["field"])

    await wm.clear_pending_gt_tap(str(session_id))
```

- [ ] **Step 5: Wire into both routes**

In `src/flashback/http/routes/turn.py`, after the `question_decision` block (line ~91), add:

```python
    if body.ground_truth_answer is not None:
        # Persist before the pipeline runs (mirrors question_decision):
        # the same-call extraction context and gap-selector must see it.
        await persist_ground_truth_answer(
            session_id=body.session_id,
            person_id=body.person_id,
            answer=body.ground_truth_answer,
            wm=wm,
            db_pool=db_pool,
        )
```

with the import `from flashback.http.ground_truth_answer import persist_ground_truth_answer`. In `src/flashback/http/routes/stream.py`, locate the pre-stream `question_decision` persistence block for `/turn/stream` and add the identical call (same imports; the stream route already has `wm` and `db_pool` deps — verify and reuse).

- [ ] **Step 6: Run the new test + http suite**

Run: `python -m pytest tests/http/ -v`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/flashback/http/ tests/http/test_ground_truth_answer.py
git commit -m "feat(ground-truth): structured ground_truth_answer sidecar on /turn and /turn/stream"
```

---

### Task 10: Segment anchor through the extraction queue

**Files:**
- Modify: `src/flashback/queues/extraction.py`
- Modify: `src/flashback/workers/extraction/schema.py` (ExtractionMessage)
- Modify: `src/flashback/orchestrator/steps/detect_segment.py:103-129`
- Modify: `src/flashback/orchestrator/steps/wrap_session.py:127-152`
- Test: `tests/queues/test_extraction_segment_anchor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/queues/test_extraction_segment_anchor.py
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from flashback.queues.extraction import ExtractionQueueProducer
from flashback.workers.extraction.schema import ExtractionMessage


@pytest.mark.asyncio
async def test_push_includes_segment_anchor_when_present():
    sqs = AsyncMock()
    sqs.send_message.return_value = "msg-1"
    producer = ExtractionQueueProducer(sqs, "https://queue")
    await producer.push(
        session_id=uuid4(), person_id=uuid4(), segment_turns=[],
        rolling_summary="", prior_rolling_summary="",
        seeded_question_id=None,
        segment_anchor={"question_text": "About when?", "answer": "1970s"},
    )
    payload = sqs.send_message.await_args.args[1]
    assert payload["segment_anchor"] == {
        "question_text": "About when?", "answer": "1970s"
    }


@pytest.mark.asyncio
async def test_push_defaults_segment_anchor_to_none():
    sqs = AsyncMock()
    sqs.send_message.return_value = "msg-1"
    producer = ExtractionQueueProducer(sqs, "https://queue")
    await producer.push(
        session_id=uuid4(), person_id=uuid4(), segment_turns=[],
        rolling_summary="", prior_rolling_summary="",
        seeded_question_id=None,
    )
    payload = sqs.send_message.await_args.args[1]
    assert payload["segment_anchor"] is None


def test_extraction_message_parses_segment_anchor():
    msg = ExtractionMessage.model_validate({
        "session_id": str(uuid4()), "person_id": str(uuid4()),
        "segment_turns": [],
        "segment_anchor": {"question_text": "About when?", "answer": "1970s"},
    })
    assert msg.segment_anchor is not None
    assert msg.segment_anchor.answer == "1970s"


def test_extraction_message_tolerates_missing_anchor():
    msg = ExtractionMessage.model_validate({
        "session_id": str(uuid4()), "person_id": str(uuid4()),
        "segment_turns": [],
    })
    assert msg.segment_anchor is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/queues/test_extraction_segment_anchor.py -v`
Expected: FAIL (`TypeError: push() got an unexpected keyword argument`)

- [ ] **Step 3: Extend producer + message schema**

`src/flashback/queues/extraction.py` — add the parameter and payload key:

```python
    async def push(
        self,
        *,
        session_id: UUID,
        person_id: UUID,
        segment_turns: list[Turn],
        rolling_summary: str,
        prior_rolling_summary: str,
        seeded_question_id: UUID | None,
        candidate_question_ids: list[UUID] | None = None,
        contributor_display_name: str = "",
        is_final: bool = False,
        segment_anchor: dict | None = None,
    ) -> str:
```

and in the payload dict:

```python
            "segment_anchor": segment_anchor,
```

`src/flashback/workers/extraction/schema.py` — after `SegmentTurn`, add:

```python
class SegmentAnchor(BaseModel):
    """A tapped time-anchor answer for the live story in this segment
    (design 2026-06-11 §4). Authoritative time evidence for the
    moment(s) of the story the question referenced."""

    model_config = ConfigDict(extra="ignore")

    question_text: str = ""
    answer: str = ""
```

and on `ExtractionMessage`:

```python
    segment_anchor: SegmentAnchor | None = None
```

- [ ] **Step 4: Carry it on both push paths**

In `src/flashback/orchestrator/steps/detect_segment.py`, extend the `deps.extraction_queue.push(...)` call:

```python
            segment_anchor=(
                {
                    "question_text": wm_state.segment_anchor_question,
                    "answer": wm_state.segment_anchor_answer,
                }
                if wm_state.segment_anchor_answer
                else None
            ),
```

and after the post-push WM mutations (after `increment_segments_pushed`), add:

```python
    if wm_state.segment_anchor_answer:
        await deps.working_memory.clear_segment_anchor(str(state.session_id))
```

In `src/flashback/orchestrator/steps/wrap_session.py` `_force_close_segment`, make the same two changes around the `is_final=True` push (lines ~128-152).

- [ ] **Step 5: Run the new test + queue/orchestrator suites**

Run: `python -m pytest tests/queues/ tests/orchestrator/ -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/flashback/queues/extraction.py src/flashback/workers/extraction/schema.py src/flashback/orchestrator/steps/detect_segment.py src/flashback/orchestrator/steps/wrap_session.py tests/queues/test_extraction_segment_anchor.py
git commit -m "feat(ground-truth): carry segment time-anchor answers through the extraction queue"
```

---

### Task 11: Extraction worker — observations in, grounding + anchor in the prompt

**Files:**
- Modify: `src/flashback/workers/extraction/schema.py` (ExtractionResult)
- Modify: `src/flashback/workers/extraction/prompts.py` (tool schema + system prompt)
- Modify: `src/flashback/workers/extraction/extraction_llm.py` (`run_extraction`, `_build_user_message`)
- Modify: `src/flashback/workers/extraction/worker.py:262-295` (fetch GT, pass through) and `:334-392` (persist observations + era_span in tx)
- Test: `tests/workers/extraction/test_ground_truth_observations.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/workers/extraction/test_ground_truth_observations.py
from flashback.workers.extraction.extraction_llm import _build_user_message
from flashback.workers.extraction.prompts import EXTRACTION_TOOL
from flashback.workers.extraction.schema import (
    ExtractionResult,
    SegmentAnchor,
)


def test_extraction_result_parses_observations():
    result = ExtractionResult.model_validate({
        "moments": [], "entities": [], "traits": [],
        "dropped_references": [], "extraction_notes": "",
        "ground_truth_observations": [
            {"field": "region", "value": "Karimnagar, Telangana, India",
             "confidence": "high"},
        ],
    })
    assert result.ground_truth_observations[0].field == "region"


def test_extraction_result_defaults_observations_empty():
    result = ExtractionResult.model_validate({
        "moments": [], "entities": [], "traits": [],
        "dropped_references": [], "extraction_notes": "",
    })
    assert result.ground_truth_observations == []


def test_tool_schema_includes_observations_with_field_enum():
    props = EXTRACTION_TOOL.input_schema["properties"]
    obs = props["ground_truth_observations"]
    field_enum = obs["items"]["properties"]["field"]["enum"]
    assert "region" in field_enum
    assert "era_span" not in field_enum  # code-derived, never LLM-emitted


def test_user_message_renders_ground_truth_and_anchor_blocks():
    msg = _build_user_message(
        subject_name="Ishita",
        subject_relationship=None,
        prior_rolling_summary="",
        segment_turns=[],
        ground_truth_block="region: Karimnagar, Telangana, India",
        segment_anchor=SegmentAnchor(
            question_text="About when was that?", answer="In the 1970s"
        ),
    )
    assert "<subject_ground_truth>" in msg
    assert "Karimnagar" in msg
    assert "<segment_time_anchor>" in msg
    assert "In the 1970s" in msg


def test_user_message_omits_blocks_when_absent():
    msg = _build_user_message(
        subject_name="Ishita", subject_relationship=None,
        prior_rolling_summary="", segment_turns=[],
    )
    assert "<subject_ground_truth>" not in msg
    assert "<segment_time_anchor>" not in msg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/workers/extraction/test_ground_truth_observations.py -v`
Expected: FAIL (`ValidationError: extra inputs` / `TypeError`)

- [ ] **Step 3: Extend schema.py**

After `DroppedReference` in `src/flashback/workers/extraction/schema.py`:

```python
class GroundTruthObservation(BaseModel):
    """A stable subject fact the LLM observed in this segment (design
    2026-06-11 §3a). Only high-confidence observations are persisted —
    provenance 'inferred', never overwriting explicit answers."""

    model_config = ConfigDict(extra="forbid")

    field: str
    value: str
    confidence: Literal["low", "medium", "high"]
```

On `ExtractionResult`, add:

```python
    ground_truth_observations: list[GroundTruthObservation] = Field(
        default_factory=list, max_length=6
    )
```

- [ ] **Step 4: Extend the tool schema + system prompt**

In `src/flashback/workers/extraction/prompts.py`, import the registry at the top:

```python
from flashback.ground_truth.registry import INFERRABLE_KEYS
```

In `EXTRACTION_TOOL.input_schema["properties"]`, after `"dropped_references"`, add:

```python
            "ground_truth_observations": {
                "type": "array",
                "description": (
                    "Stable facts about the SUBJECT this segment reveals "
                    "(where their life happened, rough birth decade, what "
                    "they wore, physical features, cultural background, "
                    "languages). Emit ONLY what the segment clearly "
                    "supports — confidence 'high' means you would not ask "
                    "the contributor to confirm. 0-3 typical."
                ),
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string", "enum": list(INFERRABLE_KEYS)},
                        "value": {"type": "string"},
                        "confidence": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                        },
                    },
                    "required": ["field", "value", "confidence"],
                    "additionalProperties": False,
                },
            },
```

(Do NOT add it to the `"required"` list — older worker deployments and the pydantic default keep it optional.)

In `EXTRACTION_SYSTEM_PROMPT`, after the "5. THEME TAGS" paragraph, add:

```
6. GROUND TRUTH OBSERVATIONS — stable facts about the SUBJECT (not about \
one moment): region where their life happened, approximate birth decade, \
usual attire, distinctive physical features, build, cultural background, \
languages. If <subject_ground_truth> is present in the user message, those \
fields are ALREADY KNOWN — re-emit one only to refine it with strictly more \
specific information (e.g. known "India" → observed "Karimnagar, Telangana, \
India"). Mark confidence honestly; only 'high' is persisted. A mention of \
Hyderabad means region is 'Hyderabad, Telangana, India' at high confidence; \
a guess from a name alone is never high confidence.
```

And two consumption notes in the CRITICAL RULES section:

```
- If `<subject_ground_truth>` is present, treat it as established context \
about the subject's world (region, era, setting). Ground every \
`generation_prompt` in it — a kitchen in 1960s rural Telangana, not a \
generic western kitchen.
- If `<segment_time_anchor>` is present, it is the contributor's tapped \
answer anchoring the story of THIS segment in time. Treat it as \
authoritative time evidence: set `time_anchor` / `life_period_estimate` \
on the moment(s) of the story it refers to.
```

- [ ] **Step 5: Extend `run_extraction` + `_build_user_message`**

In `src/flashback/workers/extraction/extraction_llm.py`, add to both signatures (after `entity_catalog`):

```python
    ground_truth_block: str = "",
    segment_anchor: "SegmentAnchor | None" = None,
```

(import `SegmentAnchor` from `.schema`). `run_extraction` passes both through to `_build_user_message`. In `_build_user_message`, after the `entity_catalog` block and before the `prior_rolling_summary` block, add:

```python
    if ground_truth_block.strip():
        lines.extend(
            [
                "",
                "<subject_ground_truth>",
                xml_text(ground_truth_block),
                "</subject_ground_truth>",
            ]
        )

    if segment_anchor is not None and segment_anchor.answer.strip():
        lines.extend(
            [
                "",
                "<segment_time_anchor>",
                f"question: {xml_text(segment_anchor.question_text)}",
                f"answer: {xml_text(segment_anchor.answer)}",
                "</segment_time_anchor>",
            ]
        )
```

- [ ] **Step 6: Wire the worker**

In `src/flashback/workers/extraction/worker.py`:

1. Imports:

```python
from flashback.ground_truth.render import render_ground_truth_block
from flashback.ground_truth.store import (
    apply_observations_sync,
    fetch_ground_truth_sync,
    recompute_era_span_sync,
)
```

2. In `_extract_and_persist` step 1 (inside the read-only connection, after `entity_rows`):

```python
                ground_truth = fetch_ground_truth_sync(
                    cur, str(payload.person_id)
                )
```

3. Pass to `run_extraction` (after `entity_catalog=entity_catalog,`):

```python
            ground_truth_block=render_ground_truth_block(
                ground_truth, "extraction"
            ),
            segment_anchor=payload.segment_anchor,
```

4. Inside the transaction, after `auto_unlock_rich_themes_sync(...)` and before `mark_processed(...)`:

```python
                    gt_written = apply_observations_sync(
                        cur,
                        str(payload.person_id),
                        extraction.ground_truth_observations,
                    )
                    recompute_era_span_sync(cur, str(payload.person_id))
```

5. Add `ground_truth_written=gt_written,` to the `extraction.persisted` log call.

- [ ] **Step 7: Run the new test + extraction suite**

Run: `python -m pytest tests/workers/extraction/ -v`
Expected: all pass (fix any fixture in `tests/workers/extraction/fixtures/sample_extractions.py` that constructs `ExtractionResult` with `extra="forbid"` violations — the new field has a default, so existing fixtures should pass untouched)

- [ ] **Step 8: Commit**

```bash
git add src/flashback/workers/extraction/ tests/workers/extraction/test_ground_truth_observations.py
git commit -m "feat(ground-truth): extraction emits observations; prompt grounded by subject ground truth + segment anchor"
```

---

### Task 12: Consumption — portrait, scenes, responder

**Files:**
- Modify: `src/flashback/profile_picture/prompt.py:50-100`
- Modify: `src/flashback/http/routes/profile_picture.py:102-146`
- Modify: `src/flashback/artifacts/compose.py:27-54`
- Modify: `src/flashback/http/routes/artifacts.py` (the regenerate/edit compose call sites — grep `compose_scene_prompt(` in that file)
- Modify: `src/flashback/response_generator/schema.py` (TurnContext + StarterContext)
- Modify: `src/flashback/response_generator/context.py` (render blocks)
- Modify: `src/flashback/orchestrator/steps/generate_response.py` (`build_turn_context`)
- Modify: `src/flashback/orchestrator/steps/starter_opener.py` (`PersonRow` + `fetch_person` + StarterContext build)
- Test: `tests/ground_truth/test_consumption.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ground_truth/test_consumption.py
from flashback.artifacts.compose import compose_scene_prompt
from flashback.profile_picture.prompt import compose_image_prompt
from flashback.response_generator.context import render_turn_context
from flashback.response_generator.schema import TurnContext


def test_portrait_prompt_carries_ground_truth_descriptors():
    prompt = compose_image_prompt(
        name="Ishita", gender="she", relationship="grandmother",
        ground_truth_context=(
            "from Karimnagar, Telangana, India, born in the 1950s, "
            "typically wearing cotton saree"
        ),
    )
    assert "from Karimnagar, Telangana, India" in prompt
    assert "cotton saree" in prompt
    # Existing recipe is untouched
    assert "Red Dead Redemption 2 character art" in prompt


def test_portrait_prompt_unchanged_without_ground_truth():
    a = compose_image_prompt(name="Ishita", gender="she")
    b = compose_image_prompt(name="Ishita", gender="she",
                             ground_truth_context=None)
    assert a == b


def test_scene_prompt_appends_setting_context():
    prompt = compose_scene_prompt(
        base_prompt="A wood-paneled kitchen at dawn.",
        ground_truth_context="Setting context: rural Telangana, 1960s era.",
    )
    assert prompt.startswith("A wood-paneled kitchen at dawn.")
    assert "Setting context: rural Telangana, 1960s era." in prompt


def test_turn_context_renders_subject_ground_truth_block():
    ctx = TurnContext(
        person_name="Ishita", intent="story",
        emotional_temperature="medium",
        ground_truth_block="region: Karimnagar, Telangana, India",
    )
    rendered = render_turn_context(ctx)
    assert "<subject_ground_truth>" in rendered
    assert "Karimnagar" in rendered


def test_turn_context_omits_block_when_empty():
    ctx = TurnContext(
        person_name="Ishita", intent="story",
        emotional_temperature="medium",
    )
    assert "<subject_ground_truth>" not in render_turn_context(ctx)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ground_truth/test_consumption.py -v`
Expected: FAIL (`TypeError: unexpected keyword argument 'ground_truth_context'`)

- [ ] **Step 3: Portrait composer**

In `src/flashback/profile_picture/prompt.py` `compose_image_prompt`, add the parameter:

```python
    ground_truth_context: str | None = None,
```

and insert after the `relationship` block (before the RDR2 style parts):

```python
    if ground_truth_context and ground_truth_context.strip():
        # Derived subject grounding (region/era/attire/features) — design
        # 2026-06-11 §5. Read at compose time so a manual regenerate after
        # ground truth lands produces the corrected portrait.
        parts.append(ground_truth_context.strip())
```

In `src/flashback/http/routes/profile_picture.py` `_enqueue_portrait_job`, after the `person` lookup add:

```python
    from flashback.ground_truth.render import render_ground_truth_block
    from flashback.ground_truth.store import fetch_ground_truth

    ground_truth = await fetch_ground_truth(db_pool, person_id)
```

and pass to `compose_image_prompt`:

```python
        ground_truth_context=render_ground_truth_block(ground_truth, "portrait")
        or None,
```

(`src/flashback/http/routes/persons.py` `_create_once` is left untouched — ground truth is always empty at person creation.)

- [ ] **Step 4: Scene composer**

In `src/flashback/artifacts/compose.py` `compose_scene_prompt`, add the parameter:

```python
    ground_truth_context: str | None = None,
```

and before `composed = ", ".join(parts)`:

```python
    if ground_truth_context and ground_truth_context.strip():
        parts.append(ground_truth_context.strip())
```

In `src/flashback/http/routes/artifacts.py`, find every `compose_scene_prompt(` call site (regenerate + edit paths). At each, fetch and pass the scene block (the route already has `db_pool` and `person_id` in scope):

```python
    from flashback.ground_truth.render import render_ground_truth_block
    from flashback.ground_truth.store import fetch_ground_truth

    ground_truth = await fetch_ground_truth(db_pool, person_id)
    # then on the compose call:
        ground_truth_context=render_ground_truth_block(ground_truth, "scene")
        or None,
```

(Put the imports at module top, not inline, matching the file's import style.)

- [ ] **Step 5: Responder contexts**

`src/flashback/response_generator/schema.py` — add to **both** `TurnContext` and `StarterContext`:

```python
    # Rendered ground-truth block (audience='responder'); empty = unknown.
    ground_truth_block: str = ""
```

`src/flashback/response_generator/context.py` — in `render_turn_context`, after the `_render_subject(...)` section is appended (top of the sections list), add:

```python
    if ctx.ground_truth_block.strip():
        sections.append(
            _block("subject_ground_truth", xml_text(ctx.ground_truth_block.strip()))
        )
```

Add the same three lines to `render_starter_context` right after its subject section.

`src/flashback/orchestrator/steps/starter_opener.py` — extend `PersonRow`:

```python
    ground_truth: dict = field(default_factory=dict)
```

(add `field` to the dataclasses import). Extend `fetch_person`'s SELECT to `SELECT name, relationship, phase, gender, profile_summary, ground_truth` and the unpacking accordingly (the `len(row) == 3` legacy branch keeps `ground_truth={}`; the full branch unpacks six values, defaulting `ground_truth` to `{}` when the column value is `None`). In the function that builds `StarterContext` in the same file (`build_starter_context`), set:

```python
        ground_truth_block=render_ground_truth_block(person.ground_truth, "responder"),
```

with the import `from flashback.ground_truth.render import render_ground_truth_block`. (Search the file for `StarterContext(` — there may be more than one construction site; set the field at each.)

`src/flashback/orchestrator/steps/generate_response.py` `build_turn_context` — add to the `TurnContext(...)` construction:

```python
        ground_truth_block=render_ground_truth_block(
            person.ground_truth, "responder"
        ),
```

with the same import.

- [ ] **Step 6: Coverage-tap chip generation gets the context too**

In `src/flashback/orchestrator/tap_options.py` `generate_tap_options`, add parameter `ground_truth_context: str = ""` and, when non-empty, append to `user_block`:

```python
    if ground_truth_context.strip():
        user_block += (
            f"\n<subject_ground_truth>{xml_text(ground_truth_context)}"
            "</subject_ground_truth>"
        )
```

In `src/flashback/orchestrator/steps/select_coverage_tap.py`, fetch and pass it on the `generate_tap_options(...)` call:

```python
        from flashback.ground_truth.render import render_ground_truth_block
        from flashback.ground_truth.store import fetch_ground_truth
        # (module-top imports)
        ...
        ground_truth = await fetch_ground_truth(deps.db_pool, state.person_id)
        options = await generate_tap_options(
            settings=deps.settings,
            question_text=rendered_text,
            person_name=name,
            person_relationship=relationship,
            dimension=dimension,
            ground_truth_context=render_ground_truth_block(
                ground_truth, "responder"
            ),
        )
```

- [ ] **Step 7: Run the new test + affected suites**

Run: `python -m pytest tests/ground_truth/ tests/orchestrator/ tests/response_generator/ tests/http/ -v`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add src/flashback/profile_picture/ src/flashback/artifacts/compose.py src/flashback/http/routes/ src/flashback/response_generator/ src/flashback/orchestrator/ tests/ground_truth/test_consumption.py
git commit -m "feat(ground-truth): inject ground truth into portrait/scene composition and responder context"
```

---

### Task 13: Onboarding additions — region + birth era

**Files:**
- Modify: `src/flashback/onboarding/archetypes.py` (new questions + append + GT mapping helper)
- Modify: `src/flashback/http/models.py` (`ArchetypeAnswersRequest.answers` max bound)
- Modify: `src/flashback/http/routes/onboarding.py` (persist GT writes in the same tx)
- Test: `tests/onboarding/test_ground_truth_questions.py` (create `tests/onboarding/__init__.py` if absent — check first; onboarding tests may live elsewhere, mirror existing location)

- [ ] **Step 1: Write the failing test**

```python
# tests/onboarding/test_ground_truth_questions.py
from flashback.onboarding.archetypes import (
    GROUND_TRUTH_QUESTIONS,
    expected_question_ids,
    ground_truth_writes_from_answers,
    public_questions_for_relationship,
    questions_for_archetype,
)


def test_every_archetype_gets_the_two_gt_questions():
    for archetype in ("friend", "parent", "generic"):
        ids = [q["id"] for q in questions_for_archetype(archetype)]
        assert "gt_region" in ids
        assert "gt_birth_era" in ids


def test_expected_ids_include_gt_questions():
    ids = expected_question_ids("friend")
    assert {"gt_region", "gt_birth_era"} <= ids


def test_public_questions_strip_ground_truth_field_key():
    _, questions = public_questions_for_relationship("friend")
    gt_q = next(q for q in questions if q["id"] == "gt_region")
    assert "ground_truth_field" not in gt_q
    assert all("implies" not in o for o in gt_q["options"])


def test_ground_truth_writes_from_answers():
    answers = [
        {"question_id": "gt_region", "option_id": None,
         "free_text": "Karimnagar, Telangana", "label": None},
        {"question_id": "gt_birth_era", "option_id": "era_50s_60s",
         "label": "1950s or 60s"},
        {"question_id": "friend_meet", "option_id": "school",
         "label": "Through school"},
        {"question_id": "gt_region_skipped_example", "skipped": True},
    ]
    writes = ground_truth_writes_from_answers(answers)
    assert ("region", "Karimnagar, Telangana") in writes
    assert ("birth_era", "1950s or 60s") in writes
    assert len(writes) == 2  # non-GT and skipped answers ignored
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/onboarding/test_ground_truth_questions.py -v`
Expected: FAIL with `ImportError: cannot import name 'GROUND_TRUTH_QUESTIONS'`

- [ ] **Step 3: Add the questions + helpers**

In `src/flashback/onboarding/archetypes.py`, after the `ARCHETYPES` dict, add:

```python
# --- Ground-truth onboarding questions (design 2026-06-11 §3d) ------------
# Appended to EVERY archetype set. `ground_truth_field` is server-only
# (stripped from the public payload, like `implies`). Answers route to
# persons.ground_truth with provenance='onboarding'; the implies
# machinery does not apply to these.

GROUND_TRUTH_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "gt_region",
        "text": "Where did most of their life happen?",
        "allow_free_text": True,
        "allow_skip": True,
        "ground_truth_field": "region",
        "options": [
            {"id": "same_place", "label": "Same place as me"},
            {"id": "another_town", "label": "A town they later left"},
            {"id": "another_state", "label": "Another part of the country"},
            {"id": "abroad", "label": "Another country"},
        ],
    },
    {
        "id": "gt_birth_era",
        "text": "Roughly when were they born?",
        "allow_free_text": True,
        "allow_skip": True,
        "ground_truth_field": "birth_era",
        "options": [
            {"id": "era_40s_earlier", "label": "1940s or earlier"},
            {"id": "era_50s_60s", "label": "1950s or 60s"},
            {"id": "era_70s_80s", "label": "1970s or 80s"},
            {"id": "era_90s_later", "label": "1990s or later"},
        ],
    },
]

_GT_QUESTION_FIELDS: dict[str, str] = {
    str(q["id"]): str(q["ground_truth_field"]) for q in GROUND_TRUTH_QUESTIONS
}


def ground_truth_writes_from_answers(
    answers: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    """Extract (field, value) writes from resolved onboarding answers.
    Free-text wins over chip label when both somehow present; skipped
    and non-GT answers are ignored."""
    writes: list[tuple[str, str]] = []
    for answer in answers:
        field = _GT_QUESTION_FIELDS.get(str(answer.get("question_id") or ""))
        if field is None or answer.get("skipped"):
            continue
        value = str(
            answer.get("free_text") or answer.get("label") or ""
        ).strip()
        if value:
            writes.append((field, value))
    return writes
```

Change `questions_for_archetype` to append them:

```python
def questions_for_archetype(archetype: str) -> list[dict[str, Any]]:
    base = ARCHETYPES.get(archetype, ARCHETYPES["generic"])
    return [*base, *GROUND_TRUTH_QUESTIONS]
```

In `public_questions_for_relationship`, the questions come from `deepcopy(ARCHETYPES[archetype])` — change that line to use the combined list so GT questions are returned too:

```python
    questions = deepcopy(questions_for_archetype(archetype))
```

and inside its per-question loop, also strip the server-only key:

```python
        question.pop("ground_truth_field", None)
```

(Note: GT question options have no `implies`; the existing `option.pop("implies", None)` is a safe no-op for them. `resolve_answer` / `_find_question` work unchanged because `questions_for_archetype` now includes them. `sanitize_implies(option.get("implies"))` returns the empty shape for GT options — harmless.)

- [ ] **Step 4: Widen the answers bound and persist GT writes**

In `src/flashback/http/models.py`, change `ArchetypeAnswersRequest.answers` to:

```python
    answers: list[ArchetypeAnswerInput] = Field(min_length=3, max_length=8)
```

In `src/flashback/http/routes/onboarding.py` `archetype_answers`, inside the existing transaction, after `persist_archetype_onboarding(...)`:

```python
                gt_writes = ground_truth_writes_from_answers(answers)
                for gt_field, gt_value in gt_writes:
                    await upsert_ground_truth_field(
                        cur,
                        body.person_id,
                        field=gt_field,
                        value=gt_value,
                        provenance="onboarding",
                        confidence="high",
                    )
```

with imports `from flashback.onboarding.archetypes import ground_truth_writes_from_answers` (extend the existing import block) and `from flashback.ground_truth.store import upsert_ground_truth_field`.

- [ ] **Step 5: Run the new test + onboarding/http suites**

Run: `python -m pytest tests/onboarding/ tests/http/ -v`
(If onboarding tests live under a different path, run that path — find them with `ls tests` / grep for `archetype` under `tests/`.)
Expected: all pass. If any existing onboarding test asserts an exact question count per archetype, update it (+2).

- [ ] **Step 6: Commit**

```bash
git add src/flashback/onboarding/archetypes.py src/flashback/http/ tests/onboarding/
git commit -m "feat(ground-truth): region + birth-era onboarding questions route to persons.ground_truth"
```

---

### Task 14: Docs + spec amendment

**Files:**
- Modify: `CLAUDE.md` (new invariant #26, API section, schema section)
- Modify: `API.md` (`/turn` request/response additions, onboarding question additions)
- Modify: `NODE_INTEGRATION.md` (tap `kind`/`field`, `ground_truth_answer` sidecar contract, `persons.ground_truth` read surface)
- Modify: `SCHEMA.md` (`persons.ground_truth` column + JSONB shape)
- Modify: `ARCHITECTURE.md` (ground-truth layer: capture paths + consumption diagram note)
- Modify: `docs/superpowers/specs/2026-06-11-ground-truth-capture-design.md` (migration number 0024→0026; region as free string, normalizer removed)

- [ ] **Step 1: Amend the spec**

In the spec: §6 first bullet "Migration 0024" → "Migration 0026". §1 table row 1: shape `{country, locale}` → `short free string ("Karimnagar, Telangana, India")`. §3c: delete the normalizer sentence, replace with "Answer values are stored as the tapped label or free text verbatim; prompts consume text directly."

- [ ] **Step 2: Add invariant #26 to CLAUDE.md**

Append after invariant #25, following the existing voice and density:

```markdown
26. **Ground truth is captured structured, never mined.** Stable subject
    facts (the 9-field registry in `flashback/ground_truth/registry.py`:
    region, birth_era, setting_type, attire, distinctive_features,
    build, cultural_context, era_span, languages) live in
    `persons.ground_truth` JSONB, each value carrying
    `{value, provenance, confidence, updated_at}` with precedence
    `user_edit > tap > onboarding > inferred` — inference never
    overwrites an explicit answer. Complexion/ethnicity is **not** a
    field and is never asked; prompts derive it from region + era +
    cultural context. DOB is still never stored — `birth_era` is a
    decade estimate. Capture paths: (a) the Extraction Worker emits
    `ground_truth_observations` as a byproduct (high-confidence only,
    `provenance='inferred'`); (b) one contextual tap card per session
    max, on `story`/`deepen` intents only (never `switch` — that
    surface belongs to the question bank), gated on emotional
    temperature, ≥3 user turns, and a small-LLM skip-gate that never
    asks what the conversation already revealed; (c) two onboarding
    questions (region, birth decade). Answers return as the structured
    `ground_truth_answer` sidecar on `/turn` — never as chat text, so
    extraction never mines demographic Q&A. Segment-anchor taps
    ("about when was that?") write to Working Memory and ride the
    extraction payload as `<segment_time_anchor>`; the worker treats
    them as authoritative time evidence for that segment's moments.
    Consumption is read-at-compose-time only (extraction prompt,
    portrait/scene composers, responder context) — nothing
    auto-regenerates when ground truth changes; manual regenerate is
    the recovery path.
```

Also add `ground_truth JSONB` to the CLAUDE.md §5 persons-columns notes and the `/turn` sidecar + tap `kind` to §9's `/turn` description.

- [ ] **Step 3: Update API.md / NODE_INTEGRATION.md / SCHEMA.md / ARCHITECTURE.md**

Read each file's relevant section first and match its format. Cover:
- `POST /turn` (+ `/turn/stream`): optional `ground_truth_answer` `{kind, field?, option_label?, free_text?, skipped}`; persisted pre-pipeline; ignored when no GT tap is pending.
- `metadata.taps[]` entries now carry `kind` (`coverage | ground_truth | segment_anchor`, default `coverage`) and `field` (registry key, null for coverage); `question_id` is null for non-coverage kinds — Node renders all three kinds with the same card and must NOT post `question_decision` for null-question_id taps.
- Onboarding: every archetype set now ends with `gt_region` + `gt_birth_era`; answers array bound is 3–8.
- `persons.ground_truth` JSONB shape + the rule that Node reads it but writes only via the future `POST /ground_truth/upsert` (v2, not yet implemented).

- [ ] **Step 4: Full test suite**

Run: `python -m pytest tests/ -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md API.md NODE_INTEGRATION.md SCHEMA.md ARCHITECTURE.md docs/superpowers/specs/2026-06-11-ground-truth-capture-design.md
git commit -m "docs(ground-truth): invariant #26, API/Node contract, schema docs; spec amendments"
```

---

## Self-review notes (already applied)

- **Spec coverage:** §1 registry → Task 2; §2 storage → Tasks 1+3; §3a inference → Task 11; §3b tap → Tasks 7+8; §3c sidecar → Task 9; §3d onboarding → Task 13; §4 anchors → Tasks 7 (chips), 8 (tap), 9 (WM write), 10 (payload), 11 (prompt); §5 consumption → Tasks 11 (extraction) + 12; §6 contract → Tasks 9, 10, 13, 14; §7 error handling → built into Tasks 3 (rejection), 7 (None on failure), 8 (DEGRADE policy), 9 (no-pending ignore); §8 testing → per-task tests.
- **Known judgment calls for the executor:** WM client method style must mirror `record_tap_emitted` exactly (Task 5); `IntentResult` constructor fields must be checked against `intent_classifier/schema.py` (Task 8); the `/turn/stream` route's request model and `question_decision` block location must be confirmed in `routes/stream.py` (Task 9); onboarding test location must be confirmed (Task 13).

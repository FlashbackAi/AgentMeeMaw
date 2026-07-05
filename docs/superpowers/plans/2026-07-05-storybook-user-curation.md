# Storybook User-Curated Moments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user preview which moments a storybook will draw on, exclude/add moments, and render from the confirmed set — while the auto-curate flow keeps working unchanged.

**Architecture:** A new `POST /storybooks/preview` endpoint returns the curation picks (Valkey-cached per pool fingerprint) plus the rest of the qualifying pool; `POST /storybooks` gains an optional `moment_ids` field that filters the context to the confirmed slice and sets a `user_curated` flag the worker honours by skipping its own curation. The rerender path is fixed to rebuild a user-curated slice instead of silently re-fetching the whole pool.

**Tech Stack:** Python 3.11+, FastAPI, psycopg (async), redis.asyncio / fakeredis, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-07-05-storybook-user-curation-design.md`

## Global Constraints

- Bounds: `STORYBOOK_MIN_SELECT = 5`, `STORYBOOK_MAX_SELECT = 25`; min relaxes to `STORYBOOK_MIN_MOMENTS` (3) when the pool is under 5.
- Collection slugs are `childhood`, `interesting`, `nostalgia`, `festivals`, `adventurous` (grid / `CURATED_SLUGS`) and `wisdom` (chapter). Never invent others.
- `POST /storybooks` WITHOUT `moment_ids` must behave byte-for-byte as today — every existing test must pass untouched.
- New context keys (`user_curated`, per-moment `id`) MUST default when absent so already-queued contexts deserialize mid-deploy.
- Valkey is best-effort: any cache read/write error falls back to computing inline; never raise from the cache layer.
- DB-backed tests need the local Docker containers started (`docker start` them) and `TEST_DATABASE_URL` set (Postgres on :15432); tests skip when unset. Some pre-existing test failures are known and unrelated — see `memory/test_environment.md`; judge your work only by the test files this plan touches plus the existing storybook suites.
- Line length ≤ 79 (project style, ruff); run `python -m ruff check src tests` before each commit.
- Commit trailer: `Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>` (never any other co-author trailer).

---

### Task 1: Context carries moment ids + `user_curated` flag

**Files:**
- Modify: `src/flashback/storybook/context.py`
- Modify: `src/flashback/storybook/generation.py` (`_moments_payload` only)
- Test: `tests/storybook/test_context.py` (append)

**Interfaces:**
- Produces: `StorybookRenderContext.user_curated: bool` (default `False`); `build_context_dict(..., user_curated: bool = False)`; `_moments_payload` items carry `"id": str`.
- Consumed by: Tasks 2, 5, 6.

- [ ] **Step 1: Write the failing tests** — append to `tests/storybook/test_context.py`:

```python
def test_context_user_curated_defaults_false_on_old_dicts() -> None:
    """A context written before this feature deserializes unchanged."""
    ctx = StorybookRenderContext.from_dict(
        {"collection": "childhood", "subject_name": "Dad"},
        storybook_id="sb1",
        person_id="p1",
    )
    assert ctx.user_curated is False


def test_context_round_trips_user_curated_and_ids() -> None:
    d = build_context_dict(
        collection="childhood",
        subject_name="Dad",
        relationship="father",
        gt_context="",
        moments=[{"id": "m-1", "title": "t", "narrative": "n",
                  "life_period": "", "time_anchor": None}],
        pdf_put_url="u",
        cover_put_url="u",
        page_put_urls=["u"] * 7,
        user_curated=True,
    )
    assert d["user_curated"] is True
    ctx = StorybookRenderContext.from_dict(
        d, storybook_id="sb1", person_id="p1"
    )
    assert ctx.user_curated is True
    assert ctx.moments[0]["id"] == "m-1"
```

Ensure the file imports `StorybookRenderContext` and `build_context_dict` from `flashback.storybook.context` (add to the existing import if missing).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/storybook/test_context.py -v -k user_curated`
Expected: FAIL — `build_context_dict() got an unexpected keyword argument 'user_curated'` and/or `AttributeError: user_curated`.

- [ ] **Step 3: Implement** — in `src/flashback/storybook/context.py`:

In `StorybookRenderContext`, after the `reuse_script: bool = False` field add:

```python
    # True when the family confirmed the moment slice in the preview;
    # the worker must NOT re-curate (spec 2026-07-05).
    user_curated: bool = False
```

In `from_dict`, after the `reuse_script=...` line add:

```python
            user_curated=bool(d.get("user_curated") or False),
```

In `build_context_dict`, add the keyword parameter `user_curated: bool = False` (after `reuse_script: bool = False`) and the dict entry `"user_curated": user_curated,` (after the `"reuse_script"` entry).

In `src/flashback/storybook/generation.py`, `_moments_payload`: add `"id": str(m.get("id") or ""),` as the first key of the per-moment dict.

- [ ] **Step 4: Run the storybook suite**

Run: `python -m pytest tests/storybook/test_context.py tests/storybook/test_generation_db.py -v`
Expected: PASS (the generation tests tolerate the extra `id` key — they assert presence of keys, not exact dict shape; if any asserts exact moment payload keys, update that assertion to include `id`).

- [ ] **Step 5: Commit**

```powershell
git add src/flashback/storybook/context.py src/flashback/storybook/generation.py tests/storybook/test_context.py
git commit -m @'
feat(storybook): context carries moment ids + user_curated flag

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>
'@
```

---

### Task 2: Selection constants + confirm path (`moment_ids` on generate)

**Files:**
- Modify: `src/flashback/storybook/repository.py` (constants + helper)
- Modify: `src/flashback/storybook/generation.py`
- Modify: `src/flashback/http/models.py` (`StorybookGenerateRequest`)
- Modify: `src/flashback/http/routes/storybooks.py` (create route error mapping)
- Test: `tests/storybook/test_generation_db.py` (append)

**Interfaces:**
- Consumes: Task 1's `user_curated` context plumbing.
- Produces: `STORYBOOK_MIN_SELECT = 5`, `STORYBOOK_MAX_SELECT = 25`, `effective_min_select(pool_size: int) -> int` in `repository.py`; exceptions `StorybookBadMomentIds(bad_ids: list[str])` and `StorybookSelectionOutOfBounds(got, min_select, max_select)` in `generation.py`; `generate_storybook(..., moment_ids: list[str] | None = None)`.

- [ ] **Step 1: Write the failing tests** — append to `tests/storybook/test_generation_db.py` (helpers `_make_person`, `_add_qualifying_moments`, `_urls`, `_queue` already exist at the top of this file; also add `StorybookBadMomentIds`, `StorybookSelectionOutOfBounds` to the existing `flashback.storybook.generation` import and `json` to stdlib imports):

```python
async def _pool_ids(pool, person_id: str) -> list[str]:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id::text FROM moments WHERE person_id = %s "
                "ORDER BY created_at",
                (person_id,),
            )
            return [r[0] for r in await cur.fetchall()]


async def test_confirmed_selection_filters_context_and_seeds_scene_ids(
    async_pool,
) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 8)
    ids = await _pool_ids(async_pool, pid)
    chosen = ids[:5]
    result = await generate_storybook(
        db_pool=async_pool, queue=_queue(), person_id=pid,
        collection="childhood", moment_ids=chosen, **_urls(),
    )
    assert result.moments_count == 5
    row = await _fetch_row(async_pool, result.storybook_id)
    ctx = row[2]["storybook"]
    assert ctx["user_curated"] is True
    assert [m["id"] for m in ctx["moments"]] == chosen
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT scene_moment_ids FROM storybooks WHERE id = %s",
                (result.storybook_id,),
            )
            scene_ids = (await cur.fetchone())[0]
    assert [str(s) for s in scene_ids] == chosen


async def test_selection_with_non_pool_id_rejected(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 6)
    ids = await _pool_ids(async_pool, pid)
    from uuid import uuid4
    with pytest.raises(StorybookBadMomentIds):
        await generate_storybook(
            db_pool=async_pool, queue=None, person_id=pid,
            collection="childhood",
            moment_ids=ids[:4] + [str(uuid4())], **_urls(),
        )


async def test_selection_bounds_enforced(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 8)
    ids = await _pool_ids(async_pool, pid)
    with pytest.raises(StorybookSelectionOutOfBounds):
        await generate_storybook(
            db_pool=async_pool, queue=None, person_id=pid,
            collection="childhood", moment_ids=ids[:4], **_urls(),
        )


async def test_thin_pool_relaxes_min_to_floor(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 4)
    ids = await _pool_ids(async_pool, pid)
    result = await generate_storybook(
        db_pool=async_pool, queue=_queue(), person_id=pid,
        collection="childhood", moment_ids=ids[:3], **_urls(),
    )
    assert result.moments_count == 3


async def test_selection_deduped_before_bounds(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 8)
    ids = await _pool_ids(async_pool, pid)
    result = await generate_storybook(
        db_pool=async_pool, queue=_queue(), person_id=pid,
        collection="childhood",
        moment_ids=ids[:5] + [ids[0]], **_urls(),
    )
    assert result.moments_count == 5


async def test_no_moment_ids_keeps_auto_path(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 6)
    result = await generate_storybook(
        db_pool=async_pool, queue=_queue(), person_id=pid,
        collection="childhood", **_urls(),
    )
    row = await _fetch_row(async_pool, result.storybook_id)
    ctx = row[2]["storybook"]
    assert ctx["user_curated"] is False
    assert result.moments_count == 6
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/storybook/test_generation_db.py -v -k "selection or thin_pool_relaxes or no_moment_ids"`
Expected: FAIL — `ImportError` on the new exception names / `unexpected keyword argument 'moment_ids'`.

- [ ] **Step 3: Implement repository constants** — in `src/flashback/storybook/repository.py`, directly below `STORYBOOK_CANDIDATE_LIMIT = 40`:

```python
# User-confirmed selection bounds (preview flow, spec 2026-07-05). The min
# relaxes to the pool floor when the whole qualifying pool is under 5 so
# thin legacies are not locked out of the preview.
STORYBOOK_MIN_SELECT = 5
STORYBOOK_MAX_SELECT = 25


def effective_min_select(pool_size: int) -> int:
    """Minimum confirmable selection for a pool of ``pool_size``."""
    if pool_size >= STORYBOOK_MIN_SELECT:
        return STORYBOOK_MIN_SELECT
    return STORYBOOK_MIN_MOMENTS
```

- [ ] **Step 4: Implement the confirm path** — in `src/flashback/storybook/generation.py`:

Extend the repository import with `STORYBOOK_MAX_SELECT` and `effective_min_select`. Add after `StorybookIdConflict`:

```python
class StorybookBadMomentIds(Exception):
    """Raised when a confirmed selection contains ids outside the pool."""

    def __init__(self, bad_ids: list[str]) -> None:
        self.bad_ids = list(bad_ids)
        shown = ", ".join(self.bad_ids[:5])
        super().__init__(f"unknown or non-qualifying moment ids: {shown}")


class StorybookSelectionOutOfBounds(Exception):
    """Raised when a confirmed selection misses the min/max bounds."""

    def __init__(self, got: int, min_select: int, max_select: int) -> None:
        super().__init__(
            f"pick between {min_select} and {max_select} moments "
            f"(got {got})"
        )


def _resolve_selection(
    pool: list[dict[str, Any]], moment_ids: list[str]
) -> list[dict[str, Any]]:
    """Dedupe + resolve the confirmed ids against the qualifying pool.

    Pool membership is the validation (person-scoping falls out of it);
    unknown ids raise rather than silently dropping -- an explicit user
    choice must never be quietly ignored.
    """
    by_id = {str(m["id"]): m for m in pool}
    seen: set[str] = set()
    ordered: list[dict[str, Any]] = []
    bad: list[str] = []
    for mid in (str(m) for m in moment_ids):
        if mid in seen:
            continue
        seen.add(mid)
        if mid in by_id:
            ordered.append(by_id[mid])
        else:
            bad.append(mid)
    if bad:
        raise StorybookBadMomentIds(bad)
    lo = effective_min_select(len(pool))
    if not (lo <= len(ordered) <= STORYBOOK_MAX_SELECT):
        raise StorybookSelectionOutOfBounds(
            len(ordered), lo, STORYBOOK_MAX_SELECT
        )
    return ordered
```

In `_context(...)`: add keyword parameter `user_curated: bool = False` and pass `user_curated=user_curated` through to `build_context_dict`.

In `generate_storybook(...)`: add parameter `moment_ids: list[str] | None = None` (after `storybook_id`). After the `_fetch_inputs` call insert:

```python
    user_curated = moment_ids is not None
    if user_curated:
        moments = _resolve_selection(moments, moment_ids)
```

Pass `user_curated=user_curated` to `_context`, and change the insert call's `scene_moment_ids=[]` to:

```python
                    scene_moment_ids=(
                        [str(m["id"]) for m in moments]
                        if user_curated
                        else []
                    ),
```

Also change `moments_count=len(moments)` — already correct since `moments` was reassigned; verify the `log.info` and `StorybookGenerationResult` use `len(moments)` after the reassignment (they do — they sit below it).

- [ ] **Step 5: Wire the request model and route** — in `src/flashback/http/models.py`, `StorybookGenerateRequest` gains:

```python
    # Optional user-confirmed moment selection from the preview flow.
    # Absent = auto-curate exactly as before (spec 2026-07-05).
    moment_ids: list[UUID] | None = Field(default=None, max_length=64)
```

In `src/flashback/http/routes/storybooks.py`: extend the generation import with `StorybookBadMomentIds` and `StorybookSelectionOutOfBounds`. In `create_storybook`, pass:

```python
            moment_ids=(
                [str(m) for m in body.moment_ids]
                if body.moment_ids is not None
                else None
            ),
```

and extend the handlers: add `StorybookBadMomentIds` to the 400 `except` tuple and `StorybookSelectionOutOfBounds` to the 409 tuple.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/storybook/test_generation_db.py tests/http/test_storybook_collections.py -v`
Expected: PASS, including every pre-existing test in those files.

- [ ] **Step 7: Lint + commit**

```powershell
python -m ruff check src tests
git add src/flashback/storybook/repository.py src/flashback/storybook/generation.py src/flashback/http/models.py src/flashback/http/routes/storybooks.py tests/storybook/test_generation_db.py
git commit -m @'
feat(storybook): optional moment_ids confirm path on POST /storybooks

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>
'@
```

---

### Task 3: Fingerprinted curation cache (Valkey)

**Files:**
- Create: `src/flashback/storybook/curation_cache.py`
- Test: `tests/storybook/test_curation_cache.py` (new)

**Interfaces:**
- Consumes: `flashback.storybook.curation.curate_moments` (existing; returns `{grid_slug: [pool_index, ...]}`).
- Produces: `curation_cache_key(person_id: str) -> str` (`storybook_curation:{person_id}`); `pool_fingerprint(moments: list[dict]) -> str`; `async cached_assignments(redis, *, settings, person_id, subject_name, relationship, moments) -> dict[str, list[str]]` — grid slug → ordered **moment ids**.

- [ ] **Step 1: Write the failing tests** — create `tests/storybook/test_curation_cache.py`:

```python
"""Valkey-cached curation assignments (spec 2026-07-05).

The cache is keyed by a fingerprint of the qualifying pool's moment ids:
match -> reuse, mismatch/miss -> one curate_moments call, Valkey errors ->
compute inline. Assignments are stored by moment ID so they survive pool
reordering.
"""

from __future__ import annotations

import json

import fakeredis.aioredis
import pytest
import pytest_asyncio

from flashback.storybook import curation_cache
from flashback.storybook.curation_cache import (
    cached_assignments,
    curation_cache_key,
    pool_fingerprint,
)

_MOMENTS = [
    {"id": f"m-{i}", "title": f"t{i}", "narrative": "n"} for i in range(4)
]


@pytest_asyncio.fixture
async def redis():
    client = fakeredis.aioredis.FakeRedis()
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def fake_curate(monkeypatch):
    calls: list[dict] = []

    async def _fake(**kwargs):
        calls.append(kwargs)
        return {
            "childhood": [0, 2],
            "interesting": [1],
            "nostalgia": [],
            "festivals": [],
            "adventurous": [3],
        }

    monkeypatch.setattr(curation_cache, "curate_moments", _fake)
    return calls


def test_fingerprint_is_order_insensitive() -> None:
    assert pool_fingerprint(_MOMENTS) == pool_fingerprint(_MOMENTS[::-1])
    assert pool_fingerprint(_MOMENTS) != pool_fingerprint(_MOMENTS[:3])


async def test_miss_curates_and_caches_by_moment_id(
    redis, fake_curate
) -> None:
    got = await cached_assignments(
        redis, settings=object(), person_id="p1", subject_name="Dad",
        relationship="father", moments=_MOMENTS,
    )
    assert got["childhood"] == ["m-0", "m-2"]
    assert len(fake_curate) == 1
    raw = json.loads(await redis.get(curation_cache_key("p1")))
    assert raw["fingerprint"] == pool_fingerprint(_MOMENTS)
    assert raw["assignments"]["adventurous"] == ["m-3"]


async def test_hit_skips_the_llm(redis, fake_curate) -> None:
    await cached_assignments(
        redis, settings=object(), person_id="p1", subject_name="Dad",
        relationship=None, moments=_MOMENTS,
    )
    again = await cached_assignments(
        redis, settings=object(), person_id="p1", subject_name="Dad",
        relationship=None, moments=list(reversed(_MOMENTS)),
    )
    assert len(fake_curate) == 1
    assert again["childhood"] == ["m-0", "m-2"]


async def test_pool_change_recurates(redis, fake_curate) -> None:
    await cached_assignments(
        redis, settings=object(), person_id="p1", subject_name="Dad",
        relationship=None, moments=_MOMENTS,
    )
    grown = _MOMENTS + [{"id": "m-9", "title": "t9", "narrative": "n"}]
    await cached_assignments(
        redis, settings=object(), person_id="p1", subject_name="Dad",
        relationship=None, moments=grown,
    )
    assert len(fake_curate) == 2


async def test_redis_errors_fall_back_to_inline_curation(
    fake_curate,
) -> None:
    class Boom:
        async def get(self, *_a, **_k):
            raise ConnectionError("valkey down")

        async def set(self, *_a, **_k):
            raise ConnectionError("valkey down")

    got = await cached_assignments(
        Boom(), settings=object(), person_id="p1", subject_name="Dad",
        relationship=None, moments=_MOMENTS,
    )
    assert got["childhood"] == ["m-0", "m-2"]
    assert len(fake_curate) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/storybook/test_curation_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: flashback.storybook.curation_cache`.

- [ ] **Step 3: Implement** — create `src/flashback/storybook/curation_cache.py`:

```python
"""Valkey-cached storybook curation assignments (spec 2026-07-05).

One Sonnet pass assigns the qualifying pool across all five grid
collections; the result is cached per person, keyed by a fingerprint of
the pool's moment ids. New extracted moments change the fingerprint and
self-invalidate -- no DEL hook anywhere. Cache-aside like the entity-name
cache (invariant #20's pattern); the cache is derived, recomputable state
(invariant #7): any Valkey failure just costs one inline curation call.

Assignments are stored by moment ID, not pool index, so a cached
assignment survives pool reordering between calls.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import structlog

from flashback.storybook.curation import curate_moments

log = structlog.get_logger("flashback.storybook.curation_cache")

CURATION_CACHE_TTL_SECONDS = 7 * 24 * 3600


def curation_cache_key(person_id: str) -> str:
    return f"storybook_curation:{person_id}"


def pool_fingerprint(moments: list[dict[str, Any]]) -> str:
    """sha256 over the sorted moment ids -- order-insensitive."""
    ids = sorted(str(m.get("id") or "") for m in moments)
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()


async def cached_assignments(
    redis,
    *,
    settings: Any,
    person_id: str,
    subject_name: str,
    relationship: str | None,
    moments: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Grid slug -> ordered moment ids, cache-aside on the fingerprint."""
    key = curation_cache_key(str(person_id))
    fp = pool_fingerprint(moments)
    raw = None
    try:
        raw = await redis.get(key)
    except Exception:
        log.warning("storybook.curation_cache_read_failed", exc_info=True)
    if raw:
        try:
            cached = json.loads(raw)
            if cached.get("fingerprint") == fp:
                return {
                    slug: [str(i) for i in ids]
                    for slug, ids in (cached.get("assignments") or {}).items()
                }
        except (ValueError, AttributeError):
            log.warning("storybook.curation_cache_bad_payload")
    by_index = await curate_moments(
        settings=settings,
        subject_name=subject_name,
        relationship=relationship,
        moments=moments,
    )
    assignments = {
        slug: [str(moments[i]["id"]) for i in idxs]
        for slug, idxs in by_index.items()
    }
    try:
        await redis.set(
            key,
            json.dumps({"fingerprint": fp, "assignments": assignments}),
            ex=CURATION_CACHE_TTL_SECONDS,
        )
    except Exception:
        log.warning("storybook.curation_cache_write_failed", exc_info=True)
    return assignments
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/storybook/test_curation_cache.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Lint + commit**

```powershell
python -m ruff check src/flashback/storybook/curation_cache.py tests/storybook/test_curation_cache.py
git add src/flashback/storybook/curation_cache.py tests/storybook/test_curation_cache.py
git commit -m @'
feat(storybook): fingerprinted Valkey cache for curation assignments

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>
'@
```

---

### Task 4: Preview builder + `POST /storybooks/preview`

**Files:**
- Modify: `src/flashback/storybook/repository.py` (add `fetch_storybook_usage_async`)
- Create: `src/flashback/storybook/preview.py`
- Modify: `src/flashback/http/models.py` (preview request/response models)
- Modify: `src/flashback/http/routes/storybooks.py` (preview route)
- Test: `tests/storybook/test_preview.py` (new), `tests/http/test_storybook_preview_route.py` (new)

**Interfaces:**
- Consumes: Task 3's `cached_assignments`; Task 2's `effective_min_select` / `STORYBOOK_MAX_SELECT`; existing `fetch_person_for_storybook_async`, `fetch_scope_scene_moments_async`, exceptions `UnknownCollection` / `StorybookNotFound` / `StorybookTooThin` from `generation.py`.
- Produces: `async build_preview(*, db_pool, redis, settings, person_id: str, collection: str) -> dict` returning `{"collection", "bounds": {"min_select", "max_select"}, "moments": [{"id","title","snippet","life_period","picked","suggested_collection","used_in"}]}`; `fetch_storybook_usage_async(cur, *, person_id) -> dict[str, list[str]]` (moment id → collection slugs of the person's `complete` storybooks).

- [ ] **Step 1: Write the failing builder tests** — create `tests/storybook/test_preview.py`:

```python
"""build_preview: picks-first ordering, wisdom pool-inclusion, used_in
chips, bounds, and thin-pool guard (spec 2026-07-05)."""

from __future__ import annotations

import fakeredis.aioredis
import pytest
import pytest_asyncio

from flashback.storybook import preview as preview_mod
from flashback.storybook.generation import (
    StorybookTooThin,
    UnknownCollection,
)
from flashback.storybook.preview import build_preview


async def _make_person(pool, name: str = "Dad") -> str:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO persons (name, relationship) "
                    "VALUES (%s, %s) RETURNING id::text",
                    (name, "father"),
                )
                return (await cur.fetchone())[0]


async def _add_qualifying_moments(pool, person_id: str, n: int) -> None:
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                for i in range(n):
                    await cur.execute(
                        "INSERT INTO moments (person_id, title, narrative, "
                        "sensory_details) VALUES (%s, %s, %s, %s)",
                        (person_id, f"m{i}", "n", "the smell of rain"),
                    )


async def _pool_ids(pool, person_id: str) -> list[str]:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id::text FROM moments WHERE person_id = %s "
                "ORDER BY created_at",
                (person_id,),
            )
            return [r[0] for r in await cur.fetchall()]


@pytest_asyncio.fixture
async def redis():
    client = fakeredis.aioredis.FakeRedis()
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def fixed_assignments(monkeypatch):
    """Patch the cache layer so no LLM call happens; the returned closure
    lets each test set the assignment (by moment id) after ids exist."""
    holder: dict = {"assignments": {}}

    async def _fake(_redis, **_kwargs):
        return holder["assignments"]

    monkeypatch.setattr(preview_mod, "cached_assignments", _fake)
    return holder


async def test_grid_preview_picks_first_with_hints(
    async_pool, redis, fixed_assignments
) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 6)
    ids = await _pool_ids(async_pool, pid)
    fixed_assignments["assignments"] = {
        "childhood": [ids[2], ids[0]],
        "adventurous": [ids[4]],
    }
    got = await build_preview(
        db_pool=async_pool, redis=redis, settings=object(),
        person_id=pid, collection="childhood",
    )
    assert got["collection"] == "childhood"
    assert got["bounds"] == {"min_select": 5, "max_select": 25}
    rows = got["moments"]
    assert [m["id"] for m in rows[:2]] == [ids[2], ids[0]]
    assert rows[0]["picked"] and rows[1]["picked"]
    assert all(not m["picked"] for m in rows[2:])
    by_id = {m["id"]: m for m in rows}
    assert by_id[ids[4]]["suggested_collection"] == "adventurous"
    assert by_id[ids[1]]["suggested_collection"] is None
    assert len(rows) == 6


async def test_wisdom_preview_includes_whole_pool_no_curation(
    async_pool, redis, monkeypatch
) -> None:
    async def _boom(*_a, **_k):
        raise AssertionError("wisdom must not curate")

    monkeypatch.setattr(preview_mod, "cached_assignments", _boom)
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 4)
    got = await build_preview(
        db_pool=async_pool, redis=redis, settings=object(),
        person_id=pid, collection="wisdom",
    )
    assert all(m["picked"] for m in got["moments"])
    assert got["bounds"]["min_select"] == 3  # pool of 4 relaxes the min


async def test_used_in_maps_complete_books(
    async_pool, redis, fixed_assignments
) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 5)
    ids = await _pool_ids(async_pool, pid)
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO storybooks (person_id, script, "
                "scene_moment_ids, moments_count, status, collection) "
                "VALUES (%s, '{}', %s, 2, 'complete', 'festivals')",
                (pid, [ids[0], ids[1]]),
            )
            await cur.execute(
                "INSERT INTO storybooks (person_id, script, "
                "scene_moment_ids, moments_count, status, collection) "
                "VALUES (%s, '{}', %s, 1, 'generating', 'nostalgia')",
                (pid, [ids[2]]),
            )
    got = await build_preview(
        db_pool=async_pool, redis=redis, settings=object(),
        person_id=pid, collection="childhood",
    )
    by_id = {m["id"]: m for m in got["moments"]}
    assert by_id[ids[0]]["used_in"] == ["festivals"]
    assert by_id[ids[2]]["used_in"] == []  # generating != rendered


async def test_thin_pool_and_bad_collection_raise(
    async_pool, redis, fixed_assignments
) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 2)
    with pytest.raises(StorybookTooThin):
        await build_preview(
            db_pool=async_pool, redis=redis, settings=object(),
            person_id=pid, collection="childhood",
        )
    with pytest.raises(UnknownCollection):
        await build_preview(
            db_pool=async_pool, redis=redis, settings=object(),
            person_id=pid, collection="memoir",
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/storybook/test_preview.py -v`
Expected: FAIL — `ModuleNotFoundError: flashback.storybook.preview`.

- [ ] **Step 3: Implement the usage query** — in `src/flashback/storybook/repository.py`, after `fetch_moments_by_ids_async`:

```python
async def fetch_storybook_usage_async(
    cur, *, person_id: UUID | str
) -> dict[str, list[str]]:
    """moment id -> collection slugs of this person's COMPLETE storybooks.

    Feeds the preview's "also appears in X" chips (spec 2026-07-05);
    informational only, so only rendered (complete) books count.
    """
    await cur.execute(
        """
        SELECT collection, scene_moment_ids
          FROM storybooks
         WHERE person_id = %(pid)s
           AND status = 'complete'
           AND collection IS NOT NULL
        """,
        {"pid": str(person_id)},
    )
    usage: dict[str, list[str]] = {}
    for collection, scene_ids in await cur.fetchall():
        for mid in scene_ids or []:
            slugs = usage.setdefault(str(mid), [])
            if collection not in slugs:
                slugs.append(collection)
    return usage
```

- [ ] **Step 4: Implement the builder** — create `src/flashback/storybook/preview.py`:

```python
"""Build the storybook preview payload (spec 2026-07-05).

The preview shows curation's picks for one collection (pre-selected)
plus the rest of the qualifying pool, so the family can exclude/add
before the render. Grid collections read the fingerprint-cached
curation; ``wisdom`` includes the whole pool with no curation call.
"""

from __future__ import annotations

from typing import Any

from psycopg_pool import AsyncConnectionPool

from flashback.storybook.collections import COLLECTIONS, CURATED_SLUGS
from flashback.storybook.curation_cache import cached_assignments
from flashback.storybook.generation import (
    StorybookNotFound,
    StorybookTooThin,
    UnknownCollection,
)
from flashback.storybook.repository import (
    STORYBOOK_MAX_SELECT,
    STORYBOOK_MIN_MOMENTS,
    effective_min_select,
    fetch_person_for_storybook_async,
    fetch_scope_scene_moments_async,
    fetch_storybook_usage_async,
)

SNIPPET_CHARS = 200


async def build_preview(
    *,
    db_pool: AsyncConnectionPool,
    redis,
    settings: Any,
    person_id: str,
    collection: str,
) -> dict[str, Any]:
    if collection not in COLLECTIONS:
        raise UnknownCollection(collection)
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            person = await fetch_person_for_storybook_async(
                cur, person_id=person_id
            )
            if person is None:
                raise StorybookNotFound(f"person {person_id} not found")
            moments = await fetch_scope_scene_moments_async(
                cur, person_id=person_id
            )
            usage = await fetch_storybook_usage_async(
                cur, person_id=person_id
            )
    if len(moments) < STORYBOOK_MIN_MOMENTS:
        raise StorybookTooThin(
            len(moments), person_name=person.get("person_name")
        )

    by_id = {str(m["id"]): m for m in moments}
    suggested: dict[str, str] = {}
    if collection in CURATED_SLUGS:
        assignments = await cached_assignments(
            redis,
            settings=settings,
            person_id=str(person_id),
            subject_name=person.get("person_name") or "",
            relationship=person.get("person_relationship"),
            moments=moments,
        )
        for slug, ids in assignments.items():
            for mid in ids:
                suggested.setdefault(str(mid), slug)
        picked_ids = [
            str(mid)
            for mid in assignments.get(collection, [])
            if str(mid) in by_id
        ]
    else:  # chapter lens: the whole pool is in by default
        picked_ids = [str(m["id"]) for m in moments]

    picked_set = set(picked_ids)
    ordered = picked_ids + [
        str(m["id"]) for m in moments if str(m["id"]) not in picked_set
    ]
    return {
        "collection": collection,
        "bounds": {
            "min_select": effective_min_select(len(moments)),
            "max_select": STORYBOOK_MAX_SELECT,
        },
        "moments": [
            {
                "id": mid,
                "title": by_id[mid].get("title") or "",
                "snippet": (by_id[mid].get("narrative") or "")[
                    :SNIPPET_CHARS
                ],
                "life_period": by_id[mid].get("life_period") or "",
                "picked": mid in picked_set,
                "suggested_collection": suggested.get(mid),
                "used_in": usage.get(mid, []),
            }
            for mid in ordered
        ],
    }
```

- [ ] **Step 5: Run the builder tests**

Run: `python -m pytest tests/storybook/test_preview.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Write the failing route tests** — create `tests/http/test_storybook_preview_route.py`:

```python
"""POST /storybooks/preview -- thin route over build_preview; assert the
error mapping and passthrough with the builder monkeypatched (the builder
itself is covered in tests/storybook/test_preview.py)."""

from __future__ import annotations

from uuid import uuid4

from flashback.http.routes import storybooks as storybooks_route
from flashback.llm.errors import LLMError
from flashback.storybook.generation import (
    StorybookNotFound,
    StorybookTooThin,
    UnknownCollection,
)

_HEADERS = {"X-Service-Token": "test-token"}


def _body() -> dict:
    return {"person_id": str(uuid4()), "collection": "childhood"}


async def test_preview_returns_builder_payload(client, monkeypatch) -> None:
    payload = {
        "collection": "childhood",
        "bounds": {"min_select": 5, "max_select": 25},
        "moments": [{
            "id": str(uuid4()), "title": "t", "snippet": "n",
            "life_period": "", "picked": True,
            "suggested_collection": "childhood", "used_in": [],
        }],
    }

    async def _fake(**_kwargs):
        return payload

    monkeypatch.setattr(storybooks_route, "build_preview", _fake)
    r = await client.post(
        "/storybooks/preview", json=_body(), headers=_HEADERS
    )
    assert r.status_code == 200
    assert r.json() == payload


async def test_preview_error_mapping(client, monkeypatch) -> None:
    cases = [
        (UnknownCollection("memoir"), 400),
        (StorybookNotFound("nope"), 404),
        (StorybookTooThin(2), 409),
        (LLMError("curation failed"), 502),
    ]
    def _raiser(exc):
        async def _raise(**_kwargs):
            raise exc

        return _raise

    for exc, expected in cases:
        monkeypatch.setattr(storybooks_route, "build_preview", _raiser(exc))
        r = await client.post(
            "/storybooks/preview", json=_body(), headers=_HEADERS
        )
        assert r.status_code == expected, (exc, r.status_code)


async def test_preview_requires_service_token(client) -> None:
    r = await client.post("/storybooks/preview", json=_body())
    assert r.status_code in (401, 403)
```

- [ ] **Step 7: Run route tests to verify they fail**

Run: `python -m pytest tests/http/test_storybook_preview_route.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'build_preview'` / 404 on the route.

- [ ] **Step 8: Implement models + route** — in `src/flashback/http/models.py`, next to the other storybook models:

```python
class StorybookPreviewRequest(BaseModel):
    """Body for ``POST /storybooks/preview`` -- the curation preview."""

    person_id: UUID
    collection: str = Field(min_length=1, max_length=64)


class StorybookPreviewBounds(BaseModel):
    min_select: int
    max_select: int


class StorybookPreviewMoment(BaseModel):
    id: UUID
    title: str
    snippet: str
    life_period: str
    picked: bool
    suggested_collection: str | None = None
    used_in: list[str] = Field(default_factory=list)


class StorybookPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collection: str
    bounds: StorybookPreviewBounds
    moments: list[StorybookPreviewMoment]
```

In `src/flashback/http/routes/storybooks.py`: add imports —

```python
from flashback.http.deps import (
    get_db_pool,
    get_http_config,
    get_redis,
    get_storybook_render_queue,
)
from flashback.http.models import (
    ...,
    StorybookPreviewRequest,
    StorybookPreviewResponse,
)
from flashback.llm.errors import LLMError
from flashback.storybook.preview import build_preview
```

(also `from flashback.config import HttpConfig` under `TYPE_CHECKING` if not already importable), then the route (place it above `create_storybook`):

```python
@router.post(
    "/storybooks/preview", response_model=StorybookPreviewResponse
)
async def preview_storybook(
    body: StorybookPreviewRequest,
    db_pool: AsyncConnectionPool = Depends(get_db_pool),
    redis=Depends(get_redis),
    cfg=Depends(get_http_config),
) -> StorybookPreviewResponse:
    """Curation preview: picks pre-selected + the rest of the pool.

    Read-only -- nothing is minted or enqueued until POST /storybooks
    confirms (optionally carrying ``moment_ids``).
    """
    try:
        payload = await build_preview(
            db_pool=db_pool,
            redis=redis,
            settings=cfg,
            person_id=str(body.person_id),
            collection=body.collection,
        )
    except UnknownCollection as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except StorybookNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except StorybookTooThin as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except LLMError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"curation failed: {exc}",
        ) from exc
    return StorybookPreviewResponse(**payload)
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `python -m pytest tests/http/test_storybook_preview_route.py tests/storybook/test_preview.py tests/http/test_storybook_collections.py -v`
Expected: PASS.

- [ ] **Step 10: Lint + commit**

```powershell
python -m ruff check src tests
git add src/flashback/storybook/preview.py src/flashback/storybook/repository.py src/flashback/http/models.py src/flashback/http/routes/storybooks.py tests/storybook/test_preview.py tests/http/test_storybook_preview_route.py
git commit -m @'
feat(storybook): POST /storybooks/preview -- curation picks + pool

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>
'@
```

---

### Task 5: Worker honours `user_curated` (skip curation)

**Files:**
- Modify: `src/flashback/workers/storybook_render/worker.py` (`select_moments`, `_curate_and_assemble`)
- Test: `tests/storybook/test_worker.py` (append)

**Interfaces:**
- Consumes: Task 1's `StorybookRenderContext.user_curated`.
- Produces: no new symbols — behavior change only: `user_curated` contexts never call `curate_moments`; `select_moments` returns `ctx.moments` unchanged for them.

- [ ] **Step 1: Write the failing tests** — append to `tests/storybook/test_worker.py` (reuse that file's existing context-construction helper if one exists; otherwise construct `StorybookRenderContext` directly as below, importing it and the worker module at the top of the file if not already imported):

```python
def _ctx(collection: str, *, user_curated: bool) -> StorybookRenderContext:
    return StorybookRenderContext(
        storybook_id="sb1", person_id="p1", collection=collection,
        subject_name="Dad", relationship="father", gt_context="",
        pdf_put_url="u", cover_put_url="u", page_put_urls=["u"] * 7,
        moments=[{"id": f"m-{i}", "title": f"t{i}", "narrative": "n"}
                 for i in range(6)],
        user_curated=user_curated,
    )


def test_select_moments_returns_all_for_user_curated() -> None:
    ctx = _ctx("childhood", user_curated=True)
    got = worker.select_moments(ctx, {"childhood": [0, 1]})
    assert got == ctx.moments


async def test_curate_and_assemble_skips_llm_curation_when_user_curated(
    monkeypatch,
) -> None:
    async def _boom(**_kwargs):
        raise AssertionError("must not curate a user-curated book")

    captured: dict = {}

    async def _fake_assemble(**kwargs):
        captured.update(kwargs)
        return "SCRIPT"

    monkeypatch.setattr(worker, "curate_moments", _boom)
    monkeypatch.setattr(worker, "assemble_script", _fake_assemble)
    ctx = _ctx("childhood", user_curated=True)
    got = await worker._curate_and_assemble(ctx, settings=object())
    assert got == "SCRIPT"
    assert captured["moments"] == ctx.moments


async def test_curate_and_assemble_still_curates_auto_books(
    monkeypatch,
) -> None:
    calls: list[int] = []

    async def _fake_curate(**kwargs):
        calls.append(1)
        return {"childhood": [1, 3]}

    async def _fake_assemble(**kwargs):
        return "SCRIPT"

    monkeypatch.setattr(worker, "curate_moments", _fake_curate)
    monkeypatch.setattr(worker, "assemble_script", _fake_assemble)
    ctx = _ctx("childhood", user_curated=False)
    await worker._curate_and_assemble(ctx, settings=object())
    assert calls == [1]
```

(If the file doesn't already do so, add `from flashback.storybook.context import StorybookRenderContext` and `from flashback.workers.storybook_render import worker`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/storybook/test_worker.py -v -k user_curated`
Expected: FAIL — `select_moments` returns the curated slice, and `_curate_and_assemble` hits the `_boom` curate.

- [ ] **Step 3: Implement** — in `src/flashback/workers/storybook_render/worker.py`:

`select_moments`: add as the FIRST branch of the function body (before the `CURATED_SLUGS` check), and extend the docstring's first paragraph with the sentence "User-curated contexts carry exactly the confirmed slice and pass through untouched.":

```python
    if ctx.user_curated:
        return ctx.moments
```

`_curate_and_assemble`: change the curation condition to

```python
    if ctx.collection in CURATED_SLUGS and not ctx.user_curated:
```

(the `else: curation = {}` branch already covers the skip).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/storybook/test_worker.py tests/storybook/test_worker_persistence.py -v`
Expected: PASS, including all pre-existing worker tests.

- [ ] **Step 5: Lint + commit**

```powershell
python -m ruff check src/flashback/workers/storybook_render/worker.py tests/storybook/test_worker.py
git add src/flashback/workers/storybook_render/worker.py tests/storybook/test_worker.py
git commit -m @'
feat(storybook): worker skips curation for user-curated contexts

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>
'@
```

---

### Task 6: Rerender preserves the user's selection

**Files:**
- Modify: `src/flashback/storybook/repository.py` (`fetch_storybook_for_regen_async` returns the stored context)
- Modify: `src/flashback/storybook/generation.py` (`_rerender`)
- Test: `tests/storybook/test_generation_db.py` (append)

**Interfaces:**
- Consumes: Tasks 1–2 (context `user_curated` + ids; `generate_storybook(..., moment_ids=...)`); existing `fetch_moments_by_ids_async`.
- Produces: `fetch_storybook_for_regen_async` result dict gains a `"context"` key (the raw `latest_generation_context` JSONB, may be `None`); `_rerender` rebuilds a user-curated slice and propagates `user_curated` into the fresh context.

- [ ] **Step 1: Write the failing tests** — append to `tests/storybook/test_generation_db.py` (add `edit_storybook` to the generation import if not present — it already is):

```python
async def test_edit_preserves_user_selection(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 8)
    ids = await _pool_ids(async_pool, pid)
    chosen = ids[:5]
    minted = await generate_storybook(
        db_pool=async_pool, queue=_queue(), person_id=pid,
        collection="childhood", moment_ids=chosen, **_urls(),
    )
    result = await edit_storybook(
        db_pool=async_pool, queue=_queue(),
        storybook_id=minted.storybook_id, person_id=pid,
        instructions="warmer colours", prior_instructions=[],
        **_urls(),
    )
    assert result.moments_count == 5
    row = await _fetch_row(async_pool, minted.storybook_id)
    ctx = row[2]["storybook"]
    assert ctx["user_curated"] is True
    assert [m["id"] for m in ctx["moments"]] == chosen


async def test_edit_falls_back_to_pool_when_selection_guts(
    async_pool,
) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 8)
    ids = await _pool_ids(async_pool, pid)
    chosen = ids[:5]
    minted = await generate_storybook(
        db_pool=async_pool, queue=_queue(), person_id=pid,
        collection="childhood", moment_ids=chosen, **_urls(),
    )
    async with async_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE moments SET status = 'superseded' "
                "WHERE id = ANY(%s::uuid[])",
                (chosen[:3],),
            )
    result = await edit_storybook(
        db_pool=async_pool, queue=_queue(),
        storybook_id=minted.storybook_id, person_id=pid,
        instructions="warmer colours", prior_instructions=[],
        **_urls(),
    )
    row = await _fetch_row(async_pool, minted.storybook_id)
    ctx = row[2]["storybook"]
    assert ctx["user_curated"] is False  # fell back to auto
    assert result.moments_count == 5  # the 8-moment pool minus 3 superseded


async def test_edit_on_auto_book_still_uses_whole_pool(async_pool) -> None:
    pid = await _make_person(async_pool)
    await _add_qualifying_moments(async_pool, pid, 6)
    minted = await generate_storybook(
        db_pool=async_pool, queue=_queue(), person_id=pid,
        collection="childhood", **_urls(),
    )
    result = await edit_storybook(
        db_pool=async_pool, queue=_queue(),
        storybook_id=minted.storybook_id, person_id=pid,
        instructions="warmer colours", prior_instructions=[],
        **_urls(),
    )
    assert result.moments_count == 6
    row = await _fetch_row(async_pool, minted.storybook_id)
    assert row[2]["storybook"]["user_curated"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/storybook/test_generation_db.py -v -k "edit_preserves or falls_back or auto_book_still"`
Expected: `test_edit_preserves_user_selection` FAILS (context rebuilt from the whole pool: 8 moments, `user_curated` False). The other two may already pass — that's fine; they pin the fallback and auto behavior.

- [ ] **Step 3: Extend the regen fetch** — in `src/flashback/storybook/repository.py`, `fetch_storybook_for_regen_async`: add `latest_generation_context` to the SELECT column list (after `collection`) and `"context": row[6],` to the returned dict. Extend the docstring with: "``context`` is the raw ``latest_generation_context`` JSONB (may be None) — the rerender path reads ``user_curated`` + the confirmed moment ids from it."

- [ ] **Step 4: Implement selection preservation** — in `src/flashback/storybook/generation.py`, `_rerender`, replace the single line `person, moments, gt_context = await _fetch_inputs(db_pool, person_id)` with:

```python
    person, moments, gt_context = await _fetch_inputs(db_pool, person_id)
    # A user-curated book keeps its confirmed slice across regenerate /
    # edit; superseded ids fall out of fetch_moments_by_ids. If that
    # guts the slice below the floor, fall back to the full pool (auto)
    # rather than stranding the edit -- there is no post-render re-pick
    # surface (spec 2026-07-05 §5).
    stored_ctx = ((row.get("context") or {}).get(CONTEXT_KEY)) or {}
    user_curated = bool(stored_ctx.get("user_curated"))
    if user_curated:
        kept_ids = [
            str(m["id"])
            for m in (stored_ctx.get("moments") or [])
            if m.get("id")
        ]
        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                selected = await fetch_moments_by_ids_async(
                    cur, person_id=person_id, moment_ids=kept_ids
                )
        if len(selected) >= STORYBOOK_MIN_MOMENTS:
            moments = selected
        else:
            log.warning(
                "storybook.selection_below_floor_fallback",
                storybook_id=storybook_id,
                kept=len(selected),
            )
            user_curated = False
```

and pass `user_curated=user_curated` to the `_context(...)` call in `_rerender`. Add `fetch_moments_by_ids_async` to the repository import at the top of `generation.py`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/storybook/ -v`
Expected: PASS — the three new tests plus the whole storybook suite.

- [ ] **Step 6: Lint + commit**

```powershell
python -m ruff check src tests
git add src/flashback/storybook/repository.py src/flashback/storybook/generation.py tests/storybook/test_generation_db.py
git commit -m @'
fix(storybook): rerender preserves the user-confirmed moment slice

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>
'@
```

---

### Task 7: Contract docs

**Files:**
- Modify: `API.md` (storybook section)
- Modify: `NODE_INTEGRATION.md` (storybook section)
- Modify: `CLAUDE.md` (§9 endpoint list)

**Interfaces:** none — documentation only, but it IS the Node contract; wording must match the implemented shapes exactly.

- [ ] **Step 1: API.md** — in the storybooks section, add `POST /storybooks/preview` documenting: request `{person_id, collection}`; the response shape exactly as `StorybookPreviewResponse` (collection, `bounds {min_select, max_select}`, `moments[] {id, title, snippet, life_period, picked, suggested_collection, used_in}`); ordering (picked first, curation rank); errors 400 unknown collection / 404 person / 409 too thin / 502 curation LLM failure; and the note that the call is read-only + first call per pool snapshot pays one big-LLM curation (~15s), subsequent calls are cached. On `POST /storybooks`, document the optional `moment_ids` field: max 64 entries, deduped, must all come from the preview's pool (else 400), bounds min 5 (3 when the pool is under 5) / max 25 (else 409), absent = auto-curate.

- [ ] **Step 2: NODE_INTEGRATION.md** — in the storybook flow section add the preview-before-create sequence: (1) `POST /storybooks/preview`, (2) render the checklist with `picked` pre-selected, `suggested_collection` as a hint chip and `used_in` as an "also appears in X" warning chip, (3) enforce `bounds` client-side (disable confirm outside them), (4) `POST /storybooks` with `moment_ids` — or without it to skip the review entirely (the old flow is unchanged). Note the preview is stateless: nothing persists until create.

- [ ] **Step 3: CLAUDE.md §9** — add one entry after the `POST /storybooks`-related lines (match the existing style):

```markdown
- `POST /storybooks/preview` — body: `{ person_id, collection }`. Returns
  curation's picked moments (pre-selected, rank order) + the rest of the
  qualifying pool with `suggested_collection` / `used_in` chip data and
  the selection bounds. Read-only; the curation assignment is cached in
  Valkey per pool fingerprint. Confirm by passing the optional
  `moment_ids` on `POST /storybooks` (absent = auto-curate as before);
  the worker then skips curation and regenerate/edit preserve the slice.
```

- [ ] **Step 4: Commit**

```powershell
git add API.md NODE_INTEGRATION.md CLAUDE.md
git commit -m @'
docs(storybook): preview endpoint + moment_ids contract

Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>
'@
```

---

### Task 8: Full verification sweep

**Files:** none created — verification only.

- [ ] **Step 1: Run the full affected suites**

Run: `python -m pytest tests/storybook/ tests/http/ tests/queues/test_storybook_render_producer.py -v`
Expected: PASS on everything this plan touched plus all pre-existing storybook/http tests (known unrelated failures per `memory/test_environment.md` excepted — none of those are in these paths).

- [ ] **Step 2: Lint the tree**

Run: `python -m ruff check src tests`
Expected: clean.

- [ ] **Step 3: Grep for contract drift**

Run: `git grep -n "min_select" -- src API.md NODE_INTEGRATION.md CLAUDE.md`
Expected: the bounds appear consistently (5/25, floor 3) in code and all three docs.

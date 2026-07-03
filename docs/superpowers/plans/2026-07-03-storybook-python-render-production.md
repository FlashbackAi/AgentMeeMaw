# Storybook Python Render — Production Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move storybook rendering from Node into this Python agent service: user picks one of 6 collections → route stores a render context + enqueues → a new `storybook_render` worker curates moments, assembles a per-page script (Sonnet), renders Gemini-illustrated template pages with a consistent, age-controlled subject, uploads PDF + page PNGs via Node-minted presigned URLs, and fires a transactional `NOTIFY storybook_render_complete`.

**Architecture:** Mirrors the tribute pipeline exactly (context-on-row → trigger-only SQS → sync worker → presigned PUT → NOTIFY; Postgres authoritative). A shared `flashback/page_render/` core is extracted from `tribute_video` (Gemini artist + Pillow primitives); `flashback/storybook/` gains collections manifest, curation, script assembly, master character refs, scene generation (Gemini-baked lettering + gpt-5.1 verifier), template compositor, and renderer — all ported from the **validated spike** `scripts/storybook_comic_prototype/generate.py` (its prompts/logic are the tested source of truth; port verbatim unless a task says otherwise).

**Tech Stack:** Python, FastAPI, psycopg (sync in worker / async in routes), SQS (boto3), Anthropic `claude-sonnet-4-6` (script), OpenAI `gpt-5.1` (lettering verifier), Gemini `gemini-3.1-flash-image` (art), Pillow + numpy + scipy (compositing).

## Global Constraints

- **Never touch S3 directly** — worker transfers only via Node-minted presigned URLs (`flashback.tribute_video.transfer` pattern). No AWS S3 creds anywhere.
- **Never write URL columns** (`image_url`, `thumbnail_url`, `pdf_url`, `page_urls`) — Node writes them on the NOTIFY. Agent writes `generation_prompt` / `latest_generation_context` / `script` / status only.
- **Postgres is authoritative; SQS payload is trigger-only** (`job_id`, `storybook_id`, `person_id`, `composed_at`, `enqueued_at`).
- **NOTIFY is transactional** — fired in the same transaction as the status flip (CLAUDE.md invariant #25 sibling). Channel: `storybook_render_complete`.
- **Filter `status='active'` + `person_id`** in every moment query (invariants #1, #2).
- Queue env var: `STORYBOOK_RENDER_QUEUE_URL`. Config class: `StorybookRenderConfig`.
- Page count is **7 pages + 1 cover** for every collection (`PAGE_COUNT = 7`).
- Context key: `latest_generation_context['storybook']` (`CONTEXT_KEY = "storybook"`).
- Commits end with `Co-authored-by: 5mokshith <mokshithrao1481@gmail.com>` — never any other trailer.
- DB tests need dockerized Postgres on :15432 (`docker start` the containers; `TEST_DATABASE_URL`). Pre-existing stale test failures listed in the team memory are not regressions.
- The spike file `scripts/storybook_comic_prototype/generate.py` is IN THIS REPO — "port" instructions reference its exact symbols; copy the code and apply only the listed adaptations.

---

### Task 1: Shared `page_render` core (extraction refactor, no behavior change)

**Files:**
- Create: `src/flashback/page_render/__init__.py`
- Create: `src/flashback/page_render/art.py` (moved from `src/flashback/tribute_video/art.py`)
- Create: `src/flashback/page_render/primitives.py`
- Modify: `src/flashback/tribute_video/art.py` (becomes a re-export shim)
- Modify: `src/flashback/tribute_video/compose.py` (imports shared primitives)
- Test: existing `tests/tribute_video/` must pass unchanged

**Interfaces:**
- Produces: `flashback.page_render.art.Artist` (same class, same methods: `character_reference(*, name, relationship, gt_context, blend) -> Image`, `portrait_from_photo(photo, *, name, gt_context, blend, deage) -> Image`, `illustrate(art_direction, gt_context, blend, *, character_ref=None, aspect=...) -> Image`, `GeminiError`).
- Produces: `flashback.page_render.primitives` with `paper_color(img) -> tuple[int,int,int]`, `chroma_key_green(img) -> Image`, `tone_match(art, paper) -> Image`, `feather_mask(size, frac=0.06) -> Image`, `autocrop_content(img, thresh=24) -> Image`, `load_font(path, size, fallback, weight=None)`, `wrap_words(words, font, max_w) -> list[str]`.

- [ ] **Step 1:** Run the existing tribute suite to establish green baseline: `python -m pytest tests/tribute_video/ -x -q`. Record pass count.
- [ ] **Step 2:** `git mv src/flashback/tribute_video/art.py src/flashback/page_render/art.py`; create `src/flashback/page_render/__init__.py` containing `from flashback.page_render.art import Artist, GeminiError  # noqa: F401`.
- [ ] **Step 3:** Recreate `src/flashback/tribute_video/art.py` as a shim:

```python
"""Back-compat shim: the Gemini artist moved to the shared page_render core."""
from flashback.page_render.art import (  # noqa: F401
    Artist,
    GeminiError,
    build_prompt,
)
```

- [ ] **Step 4:** In `src/flashback/page_render/primitives.py`, move these functions **verbatim** from `src/flashback/tribute_video/compose.py`, renamed public: `paper_color`, `chroma_key_green`, `_tone_match`→`tone_match`, `_feather_mask`→`feather_mask`, `_autocrop_content`→`autocrop_content`, `_font`→`load_font`, `_wrap`→`wrap_words`. In `compose.py` delete the moved bodies and import with private aliases so all existing call sites compile unchanged:

```python
from flashback.page_render.primitives import (
    autocrop_content as _autocrop_content,
    chroma_key_green,
    feather_mask as _feather_mask,
    load_font as _font,
    paper_color,
    tone_match as _tone_match,
    wrap_words as _wrap,
)
```

- [ ] **Step 5:** Re-run: `python -m pytest tests/tribute_video/ tests/http/test_tribute_generate.py tests/http/test_tribute_regenerate.py -q`. Expected: same pass count as Step 1 (refactor is invisible).
- [ ] **Step 6:** Commit: `refactor(page_render): extract shared Gemini artist + Pillow primitives from tribute_video`

---

### Task 2: Migration 0035 — storybooks render columns + view + grants

**Files:**
- Create: `migrations/0035_storybook_python_render.up.sql`
- Create: `migrations/0035_storybook_python_render.down.sql`
- Test: `tests/db/test_migration_0035.py`

**Interfaces:**
- Produces columns on `storybooks`: `collection TEXT`, `pdf_url TEXT`, `page_urls JSONB NOT NULL DEFAULT '[]'`, `rendered_at TIMESTAMPTZ`, `render_error TEXT` (status CHECK from 0029 already includes `'failed'`). `active_storybooks` view re-created with the new columns appended. Node grant extended: `UPDATE (image_url, thumbnail_url, pdf_url, page_urls)`.

- [ ] **Step 1: Write the failing DB test**

```python
"""tests/db/test_migration_0035.py"""
import json


def test_storybooks_has_render_columns(db_conn):
    cols = {
        r[0]
        for r in db_conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'storybooks'"
        ).fetchall()
    }
    assert {"collection", "pdf_url", "page_urls", "rendered_at", "render_error"} <= cols


def test_active_storybooks_view_exposes_render_fields(db_conn):
    cols = {
        r[0]
        for r in db_conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'active_storybooks'"
        ).fetchall()
    }
    assert {"collection", "pdf_url", "page_urls", "rendered_at"} <= cols
```

(Use the same `db_conn` fixture pattern as the existing `tests/db/` migration tests — copy the fixture import from `tests/db/test_migration_0033.py` if one is not shared via conftest.)

- [ ] **Step 2:** Run `python -m pytest tests/db/test_migration_0035.py -q` → FAIL (columns missing).
- [ ] **Step 3: Write the migration**

```sql
-- migrations/0035_storybook_python_render.up.sql
-- ============================================================================
-- Storybooks: Python-owned render (spec 2026-06-29, validated by spike).
-- The agent's storybook_render worker renders PDF + page PNGs and uploads via
-- Node-minted presigned URLs; Node LISTENs storybook_render_complete and
-- writes pdf_url + page_urls (+ cover image_url/thumbnail_url). The old
-- artifact_generation path for storybooks is retired.
-- ============================================================================
BEGIN;

ALTER TABLE storybooks
    ADD COLUMN IF NOT EXISTS collection   TEXT,
    ADD COLUMN IF NOT EXISTS pdf_url      TEXT,
    ADD COLUMN IF NOT EXISTS page_urls    JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS rendered_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS render_error TEXT;

-- Append-only view recreation preserves the node_readonly SELECT grant.
CREATE OR REPLACE VIEW active_storybooks AS
SELECT
    id, person_id, title, status, moments_count,
    image_url, thumbnail_url, created_at, updated_at, tags,
    collection, pdf_url, page_urls, rendered_at
FROM storybooks
WHERE status <> 'superseded';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'node_readonly') THEN
        GRANT UPDATE (image_url, thumbnail_url, pdf_url, page_urls)
            ON storybooks TO node_readonly;
    END IF;
END $$;

COMMIT;
```

```sql
-- migrations/0035_storybook_python_render.down.sql
BEGIN;

CREATE OR REPLACE VIEW active_storybooks AS
SELECT
    id, person_id, title, status, moments_count,
    image_url, thumbnail_url, created_at, updated_at, tags
FROM storybooks
WHERE status <> 'superseded';

ALTER TABLE storybooks
    DROP COLUMN IF EXISTS collection,
    DROP COLUMN IF EXISTS pdf_url,
    DROP COLUMN IF EXISTS page_urls,
    DROP COLUMN IF EXISTS rendered_at,
    DROP COLUMN IF EXISTS render_error;

COMMIT;
```

- [ ] **Step 4:** Apply to the test DB (same mechanism the other migration tests use — the migration runner applies `migrations/` in order), re-run the test → PASS.
- [ ] **Step 5:** Commit: `feat(db): 0035 storybook python-render columns, view, node grants`

---

### Task 3: Collections manifest + `GET /storybook-collections`

**Files:**
- Create: `src/flashback/storybook/collections.py`
- Create: `src/flashback/storybook/assets/<slug>/{cover.png, 1.png … 7.png}` for the 6 slugs (copied from the spike)
- Modify: `src/flashback/http/routes/storybooks.py` (add the GET route)
- Modify: `src/flashback/http/models.py` (response model)
- Test: `tests/storybook/test_collections.py`, `tests/http/test_storybook_collections.py`

**Interfaces:**
- Produces: `PAGE_COUNT = 7`; `@dataclass(frozen=True) Collection(slug, display, art_style, voice, layout, tone, theme_focus, signature)` where `layout` ∈ `{"grid","chapter"}`, `tone` ∈ `{"gentle","full"}`; `COLLECTIONS: dict[str, Collection]` (childhood, interesting, nostalgia, festivals, adventurous, wisdom); `CURATED_SLUGS: list[str]` (grid-layout slugs); `asset_dir(slug) -> str` (package-data path); `public_collections() -> list[dict]` (slug, display_name, layout, page_count).

- [ ] **Step 1:** Copy template assets from the spike (normalize the cover name):

```bash
mkdir -p src/flashback/storybook/assets
for s in childhood interesting nostalgia festivals adventurous wisdom; do
  mkdir -p "src/flashback/storybook/assets/$s"
  cp scripts/storybook_comic_prototype/assets/$s/[1-7].png "src/flashback/storybook/assets/$s/"
  cp "scripts/storybook_comic_prototype/assets/$s/Cover Page.png" "src/flashback/storybook/assets/$s/cover.png"
done
```

Verify: `ls src/flashback/storybook/assets/wisdom/` shows `1.png…7.png cover.png`. Confirm package data ships (check `pyproject.toml`/`setup.cfg` `package_data` / `include_package_data`; add `"flashback.storybook": ["assets/**/*.png"]` if the project lists patterns explicitly).

- [ ] **Step 2: Write the failing tests**

```python
"""tests/storybook/test_collections.py"""
import os

from flashback.storybook.collections import (
    COLLECTIONS,
    CURATED_SLUGS,
    PAGE_COUNT,
    asset_dir,
    public_collections,
)


def test_six_collections_registered():
    assert set(COLLECTIONS) == {
        "childhood", "interesting", "nostalgia",
        "festivals", "adventurous", "wisdom",
    }


def test_layout_and_tone_taxonomy():
    assert COLLECTIONS["wisdom"].layout == "chapter"
    assert all(COLLECTIONS[s].layout == "grid" for s in CURATED_SLUGS)
    assert {COLLECTIONS[s].tone for s in COLLECTIONS} <= {"gentle", "full"}
    assert COLLECTIONS["childhood"].tone == "gentle"
    assert COLLECTIONS["interesting"].tone == "full"


def test_every_collection_ships_templates():
    for slug in COLLECTIONS:
        d = asset_dir(slug)
        assert os.path.exists(os.path.join(d, "cover.png")), slug
        for i in range(1, PAGE_COUNT + 1):
            assert os.path.exists(os.path.join(d, f"{i}.png")), (slug, i)


def test_public_surface_shape():
    rows = public_collections()
    assert len(rows) == 6
    assert {"slug", "display_name", "layout", "page_count"} <= set(rows[0])
    assert all(r["page_count"] == PAGE_COUNT for r in rows)
```

- [ ] **Step 3:** Run → FAIL (module missing).
- [ ] **Step 4: Implement `collections.py`.** Port the two dicts from the spike — `COLLECTIONS` (art_style/voice/layout/tone per slug, spike lines around `COLLECTIONS = {`) and `COLLECTION_CHARACTER` (theme_focus + signature tuples) — merged into one frozen dataclass per slug (six `Collection(...)` literals; copy every string verbatim from the spike, including the wisdom entry and the gentle-tone marks on childhood/festivals/adventurous). Add:

```python
import os
from dataclasses import dataclass

PAGE_COUNT = 7
_ASSETS = os.path.join(os.path.dirname(__file__), "assets")


@dataclass(frozen=True)
class Collection:
    slug: str
    display: str
    art_style: str
    voice: str
    layout: str          # "grid" | "chapter"
    tone: str            # "gentle" | "full"
    theme_focus: str
    signature: str


COLLECTIONS: dict[str, Collection] = { ... }  # six literals, spike strings verbatim

CURATED_SLUGS = [s for s, c in COLLECTIONS.items() if c.layout == "grid"]


def asset_dir(slug: str) -> str:
    return os.path.join(_ASSETS, slug)


def public_collections() -> list[dict]:
    return [
        {"slug": c.slug, "display_name": c.display,
         "layout": c.layout, "page_count": PAGE_COUNT}
        for c in COLLECTIONS.values()
    ]
```

- [ ] **Step 5:** Run tests → PASS.
- [ ] **Step 6: Add the GET route + model.** In `http/models.py`:

```python
class StorybookCollectionInfo(BaseModel):
    slug: str
    display_name: str
    layout: str
    page_count: int
```

In `http/routes/storybooks.py`:

```python
from flashback.http.models import StorybookCollectionInfo
from flashback.storybook.collections import public_collections


@router.get("/storybook-collections",
            response_model=list[StorybookCollectionInfo])
async def list_storybook_collections() -> list[StorybookCollectionInfo]:
    return [StorybookCollectionInfo(**c) for c in public_collections()]
```

- [ ] **Step 7: HTTP test** (`tests/http/test_storybook_collections.py`, using the existing FastAPI test-client fixture pattern from `tests/http/test_tribute_generate.py`):

```python
def test_lists_six_collections(client):
    r = client.get("/storybook-collections")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 6
    assert {c["slug"] for c in body} == {
        "childhood", "interesting", "nostalgia",
        "festivals", "adventurous", "wisdom",
    }
    assert all(c["page_count"] == 7 for c in body)
```

- [ ] **Step 8:** Run both test files → PASS. Commit: `feat(storybook): collections manifest + template assets + GET /storybook-collections`

---

### Task 4: Curation module

**Files:**
- Create: `src/flashback/storybook/curation.py`
- Test: `tests/storybook/test_curation.py`

**Interfaces:**
- Consumes: `COLLECTIONS`, `CURATED_SLUGS` from Task 3; `flashback.llm.interface` big-LLM call pattern (same as `flashback.tribute.assembly` uses).
- Produces: `async def curate_moments(*, settings, subject_name, relationship, moments: list[dict]) -> dict[str, list[int]]` — maps each grid slug to moment indices, **at most one collection per moment** (code-side dedup backstop), best-fit-first. `moments` items carry `title`, `narrative`. Pure function `dedupe_assignments(raw: dict[str, list[int]]) -> dict[str, list[int]]` exposed for unit testing.

- [ ] **Step 1: Write failing tests for the dedup backstop (pure code, no LLM):**

```python
"""tests/storybook/test_curation.py"""
from flashback.storybook.curation import dedupe_assignments


def test_moment_in_two_collections_keeps_best_rank():
    raw = {"childhood": [5, 9], "festivals": [9, 2]}
    out = dedupe_assignments(raw)
    assert 9 in out["childhood"] and 9 not in out["festivals"]


def test_rank_tie_resolves_to_first_slug_deterministically():
    raw = {"childhood": [4], "festivals": [4]}
    out = dedupe_assignments(raw)
    assert (4 in out["childhood"]) ^ (4 in out["festivals"])
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Port from the spike: the `_CURATE_TOOL` schema (properties over `CURATED_SLUGS`), the `curate()` sys_prompt (rules 1–5 verbatim: one collection per moment, spread anchors, 6–11 aim, best-first ordering, may-fit-none), and the dedup backstop loop (`best = {} ... rank < best[i][0]`) as `dedupe_assignments`. Adapt: async Anthropic call through the same `llm` interface used by `flashback.tribute.assembly.assemble_tribute_script` (tool-forced, `max_tokens=4000`); moments truncated to 300 chars of narrative as in the spike.
- [ ] **Step 4:** Add an LLM-mocked test that `curate_moments` passes only grid slugs in the tool schema and applies the backstop (patch the LLM call to return a canned `{"collections": {...}}`). Run all → PASS.
- [ ] **Step 5:** Commit: `feat(storybook): collection curation with single-assignment backstop`

---

### Task 5: Script assembly module (the validated narrative prompt)

**Files:**
- Create: `src/flashback/storybook/script.py`
- Test: `tests/storybook/test_script.py`

**Interfaces:**
- Consumes: `Collection` from Task 3.
- Produces: `@dataclass Panel(scene, text, kind, age_stage)`; `@dataclass BookPage(panels: list[Panel])`; `@dataclass BookScript(cover_title: str, pages: list[BookPage])` with `to_dict()` / `from_dict()` (round-trips the row's `script` JSONB); `async def assemble_script(*, settings, collection: Collection, subject_name, relationship, gt_context, moments, edit_instructions: list[str] | None = None) -> BookScript`. `AGE_STAGES = {"child","young","mid","old"}` re-exported.

- [ ] **Step 1: Failing round-trip + validation tests:**

```python
"""tests/storybook/test_script.py"""
import pytest

from flashback.storybook.script import BookScript


def _raw():
    return {
        "cover_title": "T",
        "pages": [
            {"panels": [
                {"scene": "s", "text": "t", "kind": "caption", "age_stage": "mid"}
            ] * 3}
        ] * 7,
    }


def test_round_trip():
    s = BookScript.from_dict(_raw())
    assert len(s.pages) == 7 and len(s.pages[0].panels) == 3
    assert BookScript.from_dict(s.to_dict()).to_dict() == s.to_dict()


def test_bad_age_stage_rejected():
    d = _raw()
    d["pages"][0]["panels"][0]["age_stage"] = "toddler"
    with pytest.raises(ValueError):
        BookScript.from_dict(d)
```

- [ ] **Step 2:** Run → FAIL. **Step 3: Implement.** Port from the spike **verbatim**: the `_TOOL` ("comic") JSON schema including required `age_stage` enum; `assemble()`'s full `sys_prompt` — the arc block (OPEN / cause-and-effect MIDDLE incl. the anti-"list trap" sentence / CLOSE-on-an-image), `theme_focus` + SIGNATURE IMAGE block, the `gentle_rule` (gated on `collection.tone == "gentle"`), text_rule for both layouts (grid 3-beat panels + chapter flowing captions), and ALL RULES bullets (named-introductions, child-simple language, open-inside-theme, stay-anchored, **throughline rule**, **spoken-by-a-present-named-person quote rule**, age_stage instruction, exact panel counts). Panel counts: `[3]*7` for grid, `[1]*7` for chapter. Add `<family_edit_requests>` block when `edit_instructions` is non-empty (mirroring the tribute assembler's edit mechanism). Async big-LLM call, tool-forced, `max_tokens=6000`.
- [ ] **Step 4:** LLM-mocked test: patch the LLM to return a canned tool payload; assert `assemble_script` returns a 7-page `BookScript` and that a `tone="gentle"` collection's rendered sys_prompt contains "NEVER show a child drinking toddy" while a `tone="full"` one does not (capture the prompt via the mock). Run → PASS.
- [ ] **Step 5:** Commit: `feat(storybook): collection-voiced script assembly with validated narrative rules`

---

### Task 6: Master character refs + scene generation

**Files:**
- Create: `src/flashback/storybook/refs.py`
- Create: `src/flashback/storybook/scenes.py`
- Test: `tests/storybook/test_refs.py`, `tests/storybook/test_scenes.py`

**Interfaces:**
- Consumes: `flashback.page_render.art` Gemini client patterns; `PIL.Image`.
- Produces (refs.py): `AGE_STAGES: dict[str,str]` (child/young/mid/old descriptors, spike verbatim), `PRIMARY_STAGE = "mid"`, `REF_STYLE` (spike verbatim), `identity_rule(subject, role) -> str` (spike verbatim **including the appearance-only/casting paragraph**), `class MasterRefs` with `build(client, *, name, gt_context, anchor_photo: Image | None) -> None` (primary first, others chained off primary + photo) and `for_stage(stage: str | None) -> Image | None`.
- Produces (scenes.py): `gen_scene(client, scene, ref, art_style, aspect, *, text="", kind="caption", subject="", role="", tries=3, verifier=None) -> Image | None`; `gen_chapter_art(client, scene, ref, art_style, aspect, *, subject, role) -> Image | None`; `gen_cover_art(client, *, name, rel, gt_context, ref, art_style) -> Image | None`; `lettering_ok(openai_client, img, expected) -> bool`.

- [ ] **Step 1: Failing tests (all model calls mocked):**

```python
"""tests/storybook/test_refs.py"""
from unittest.mock import MagicMock

from PIL import Image

from flashback.storybook.refs import MasterRefs, PRIMARY_STAGE, identity_rule


def test_identity_rule_is_appearance_only():
    r = identity_rule("Chandraiah", "Grand Father")
    assert "ONLY" in r and "APPEARANCE-ONLY" in r
    assert "do not promote him into the main action" in r


def test_for_stage_falls_back_to_primary():
    m = MasterRefs()
    img = Image.new("RGB", (4, 4))
    m._refs = {PRIMARY_STAGE: img}
    assert m.for_stage("child") is img
    assert m.for_stage(None) is img
```

```python
"""tests/storybook/test_scenes.py"""
from unittest.mock import MagicMock, patch

from PIL import Image

from flashback.storybook.scenes import gen_scene


def test_gen_scene_rerolls_on_bad_lettering():
    img = Image.new("RGB", (4, 4))
    with patch("flashback.storybook.scenes._gen_image", return_value=img) as g, \
         patch("flashback.storybook.scenes.lettering_ok",
               side_effect=[False, True]) as v:
        out = gen_scene(MagicMock(), "scene", None, "style", "16:9",
                        text="hello world", verifier=MagicMock())
    assert out is img
    assert g.call_count == 2 and v.call_count == 2


def test_gen_scene_no_text_skips_verifier():
    img = Image.new("RGB", (4, 4))
    with patch("flashback.storybook.scenes._gen_image", return_value=img), \
         patch("flashback.storybook.scenes.lettering_ok") as v:
        gen_scene(MagicMock(), "scene", None, "style", "16:9", text="")
    v.assert_not_called()
```

- [ ] **Step 2:** Run → FAIL. **Step 3: Implement by porting the spike symbols:** `_gen_image` (5× network retry with `time.sleep(2*(attempt+1))` backoff, returns None on exhaustion), `AGE_STAGES`, `REF_STYLE`, `identity_rule`, `_gen_stage_ref`, `build_master_refs` (as the `MasterRefs` class — module-global cache becomes instance state; the disk cache is dropped: refs are built once per render job), `ref_for_stage` (as `MasterRefs.for_stage`), `gen_scene` (both text_rule branches — speech bubble + caption banner with the ≥18% bottom-inset wording — plus the reroll loop), `gen_chapter_art`, `gen_cover_art`, and `lettering_ok` (gpt-5.1, `max_completion_tokens=2000`, `reasoning_effort="low"`, prompt verbatim; a verifier exception returns True). Adaptation: `anchor_photo` is passed as a `PIL.Image` (downloaded by the worker from the presigned GET URL) instead of a disk path; aspect is passed in (the compositor computes it from the box, Task 7).
- [ ] **Step 4:** Run both files → PASS. **Step 5:** Commit: `feat(storybook): age-anchored master refs, identity rule, verified scene generation`

---

### Task 7: Compositor + renderer

**Files:**
- Create: `src/flashback/storybook/compose.py`
- Create: `src/flashback/storybook/render.py`
- Test: `tests/storybook/test_compose.py`, `tests/storybook/test_render.py`

**Interfaces:**
- Consumes: `primitives` (Task 1), `collections.asset_dir/PAGE_COUNT` (Task 3), `BookScript` (Task 5), `MasterRefs` + scene gens (Task 6).
- Produces (compose.py): `green_components(path) -> tuple[list[Box], tuple[int,int]]`, `grid_boxes(path, n) -> list[Box]`, `panel_boxes(path) -> list[Box]`, `expand_box(box, W, H) -> Box`, `gemini_aspect(box) -> str`, `fit_fill(art, w, h) -> Image`, `grid_page_base(template, path) -> Image`, `fill_panel(page, art, box) -> None`, `blend_chapter(template, art, box) -> Image`, `overlay_chapter_text(page, box, text) -> None`, `make_cover(template_path, title, subtitle, art) -> Image`. (`Box = tuple[int,int,int,int]`.)
- Produces (render.py): `@dataclass StorybookRenderResult(pdf_path: str, page_paths: list[str], cover_path: str, blank_panels: list[tuple[int,int]])`; `def render_storybook(*, script: BookScript, collection: Collection, subject_name, relationship, gt_context, master_refs: MasterRefs, gemini_client, verifier, out_dir: str) -> StorybookRenderResult`.

- [ ] **Step 1: Failing compositor tests on synthetic templates** (build a small in-test PNG with pure-green `(0,255,0)` rectangles; no shipped-asset dependency):

```python
"""tests/storybook/test_compose.py"""
import numpy as np
from PIL import Image

from flashback.storybook.compose import fit_fill, gemini_aspect, green_components


def _synthetic_template(tmp_path):
    arr = np.full((400, 300, 3), 245, dtype=np.uint8)
    arr[40:160, 30:270] = (0, 255, 0)     # panel 1
    arr[200:360, 30:270] = (0, 255, 0)    # panel 2
    p = tmp_path / "t.png"
    Image.fromarray(arr).save(p)
    return str(p)


def test_green_components_finds_both_zones(tmp_path):
    boxes, (h, w) = green_components(_synthetic_template(tmp_path))
    assert (h, w) == (400, 300) and len(boxes) == 2


def test_fit_fill_exact_size():
    art = Image.new("RGB", (100, 50))
    assert fit_fill(art, 240, 120).size == (240, 120)


def test_gemini_aspect_buckets():
    assert gemini_aspect((0, 0, 160, 90)) == "16:9"
    assert gemini_aspect((0, 0, 90, 160)) == "9:16"
```

- [ ] **Step 2:** Run → FAIL. **Step 3: Port from the spike:** `_green_components`→`green_components`, `grid_boxes`, `panel_boxes`, `expand_box`, `_gemini_aspect`→`gemini_aspect` (copy its aspect-bucket table exactly), `_fit_fill`→`fit_fill`, `grid_page_base`, `fill_panel`, `blend_chapter`, `overlay_chapter_text` (font paths → `flashback/tribute_video/assets/fonts/` same files the spike used), `make_cover`. Use Task 1 primitives (`paper_color`, `tone_match`, `feather_mask`, `autocrop_content`) instead of spike-local copies.
- [ ] **Step 4: `render.py`:** port the spike's `render_collection` page loop minus DB/CLI: for each of the 7 pages pick template `asset_dir(slug)/{i}.png`, grid → `grid_page_base` + per-panel `gen_scene(ref=master_refs.for_stage(panel.age_stage), subject=..., role=...)` + `fill_panel`; chapter → `gen_chapter_art` + `blend_chapter` + `overlay_chapter_text`; cover → `gen_cover_art` + `make_cover`; track `blank_panels` (the spike's `blank` list + WARNING log via structlog); save PNGs to `out_dir`, assemble PDF (`pages[0].save(pdf, save_all=True, append_images=..., resolution=150.0)`). Renderer test: patch `gen_scene`/`gen_chapter_art`/`gen_cover_art` to return solid `Image.new` stubs, run against the **real shipped childhood + wisdom assets**, assert 1 cover + 7 pages + 1 PDF exist and `blank_panels == []`; a second test patches `gen_scene` to return `None` once and asserts it lands in `blank_panels`.
- [ ] **Step 5:** Run → PASS. **Step 6:** Commit: `feat(storybook): manifest compositor + book renderer with blank-panel accounting`

---

### Task 8: Render context, queue producer, config, app wiring

**Files:**
- Create: `src/flashback/storybook/context.py`
- Create: `src/flashback/queues/storybook_render.py`
- Modify: `src/flashback/config.py` (HttpConfig field + `StorybookRenderConfig`)
- Modify: `src/flashback/http/app.py`, `src/flashback/http/deps.py` (producer wiring + getter)
- Test: `tests/storybook/test_context.py`, `tests/queues/test_storybook_render_producer.py`

**Interfaces:**
- Produces (context.py): `CONTEXT_KEY = "storybook"`; frozen `@dataclass StorybookRenderContext(storybook_id, person_id, collection, subject_name, relationship, gt_context, moments: list[dict], pdf_put_url, cover_put_url, page_put_urls: list[str], anchor_photo_get_url: str = "", edit_instructions: list[str] = [], reuse_script: bool = False, composed_at: str = "")` with `from_dict(d, *, storybook_id, person_id)`; `build_context_dict(**same fields) -> dict`.
- Produces (queues): `class StorybookRenderQueueProducer(sqs_client, queue_url)` with `async push(*, job_id, storybook_id, person_id, composed_at) -> str | None` (exact mirror of `TributeRenderQueueProducer`; returns None when no URL).
- Produces (config): `HttpConfig.storybook_render_queue_url: str = ""` (env `STORYBOOK_RENDER_QUEUE_URL`); `@dataclass StorybookRenderConfig` — copy `TributeRenderConfig` fields, replacing `tribute_render_queue_url`→`storybook_render_queue_url`, plus `openai_api_key: str` (env `OPENAI_API_KEY`, required — lettering verifier) and `llm_small_model: str = "gpt-5.1"`; `from_env(queue_required=True)`.
- Produces (deps): `get_storybook_render_queue(request) -> StorybookRenderQueueProducer | None` reading `app.state.storybook_render_queue`.

- [ ] **Step 1: Failing tests:**

```python
"""tests/storybook/test_context.py"""
from flashback.storybook.context import (
    CONTEXT_KEY,
    StorybookRenderContext,
    build_context_dict,
)


def test_round_trip():
    d = build_context_dict(
        collection="childhood", subject_name="C", relationship="Grand Father",
        gt_context="gt", moments=[{"title": "t", "narrative": "n"}],
        pdf_put_url="p", cover_put_url="c", page_put_urls=["u"] * 7,
        anchor_photo_get_url="a", composed_at="2026-07-03T00:00:00Z",
    )
    ctx = StorybookRenderContext.from_dict(d, storybook_id="s", person_id="p1")
    assert ctx.collection == "childhood" and len(ctx.page_put_urls) == 7
    assert ctx.composed_at == "2026-07-03T00:00:00Z"
    assert CONTEXT_KEY == "storybook"
```

```python
"""tests/queues/test_storybook_render_producer.py"""
import asyncio
from unittest.mock import AsyncMock

from flashback.queues.storybook_render import StorybookRenderQueueProducer


def test_no_queue_url_returns_none():
    p = StorybookRenderQueueProducer(AsyncMock(), "")
    assert asyncio.run(p.push(job_id="j", storybook_id="s",
                              person_id="p", composed_at="t")) is None


def test_payload_is_trigger_only():
    sqs = AsyncMock()
    sqs.send_message.return_value = "mid"
    p = StorybookRenderQueueProducer(sqs, "http://q")
    asyncio.run(p.push(job_id="j", storybook_id="s",
                       person_id="p", composed_at="t"))
    payload = sqs.send_message.call_args.args[1]
    assert set(payload) == {"job_id", "storybook_id", "person_id",
                            "composed_at", "enqueued_at"}
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement all four files following the tribute twins line-for-line (context mirrors `tribute_video/context.py`; producer mirrors `queues/tribute_render.py`; config mirrors `TributeRenderConfig.from_env` incl. `_required("OPENAI_API_KEY")`; app.py wires `StorybookRenderQueueProducer` next to the tribute one, gated on `cfg.storybook_render_queue_url`).
- [ ] **Step 4:** Run → PASS. **Step 5:** Commit: `feat(storybook): render context, trigger-only queue producer, worker config, app wiring`

---

### Task 9: `storybook_render` worker

**Files:**
- Create: `src/flashback/workers/storybook_render/__init__.py`
- Create: `src/flashback/workers/storybook_render/__main__.py`
- Create: `src/flashback/workers/storybook_render/sqs_client.py`
- Create: `src/flashback/workers/storybook_render/persistence.py`
- Create: `src/flashback/workers/storybook_render/worker.py`
- Test: `tests/storybook/test_worker.py`, `tests/storybook/test_worker_persistence.py` (DB)

**Interfaces:**
- Consumes: Tasks 4–8 modules.
- Produces (persistence): `NOTIFY_CHANNEL = "storybook_render_complete"`; `load_render_context(pool, *, storybook_id, composed_at="") -> StorybookRenderContext | None` (None on missing/stale — compare `composed_at`); `save_script(pool, *, storybook_id, title, script_dict, scene_count) -> None` (writes `title`, `script`, `moments_count` stays); `mark_complete(pool, *, storybook_id, person_id, collection, pdf_present: bool, pages_present: int, cover_present: bool) -> None` — one transaction: `UPDATE storybooks SET status='complete', rendered_at=now(), updated_at=now() WHERE id=%s` + `pg_notify(NOTIFY_CHANNEL, json{event, storybook_id, person_id, collection, status:'complete', pdf_present, pages_present, cover_present})`; `mark_failed(pool, *, storybook_id, error) -> None` guarded on `status='generating'`, no NOTIFY.
- Produces (worker): `process_one(msg, *, load_context, run_render, mark_complete) -> "ok"|"skip"`; `handle_failure(msg, exc, *, max_attempts, mark_failed, ack) -> "retry"|"failed"`; `run_forever(*, pool, cfg, sqs, stop=None)`; `render_and_upload(ctx, *, cfg, pool, tmpdir) -> tuple[bool, int, bool]` which: (1) fetches subject GT + qualifying moments already ON the context; (2) if `ctx.reuse_script` and the row has a script → use it, else `curate_moments` (grid slugs) → pick `ctx.collection`'s slice (chapter collections use the full pool) → `assemble_script` (with `ctx.edit_instructions`) → `save_script`; (3) `MasterRefs().build(...)` with `anchor_photo = transfer.download_image(ctx.anchor_photo_get_url)` when set, else None; (4) `render_storybook(...)`; (5) `transfer.upload_file` the PDF (`application/pdf`), cover + each page PNG (`image/png`) to their presigned URLs; returns `(pdf_ok, n_pages_uploaded, cover_ok)`.
- Produces (sqs_client): `StorybookRenderMessage(job_id, storybook_id, person_id, composed_at, receipt_handle, receive_count)` + `SQSClient(queue_url, region).receive(...)/delete(...)` — copy `workers/tribute_render/sqs_client.py`, renaming `tribute_id`→`storybook_id`.
- Produces (__main__): CLI mirroring tribute's — default queue drain + `run-once --storybook-id <uuid>` (queue optional) — using `StorybookRenderConfig.from_env`.

- [ ] **Step 1: Failing unit tests for the orchestration seams** (mirror `tests/tribute_video/test_worker.py` cases):

```python
"""tests/storybook/test_worker.py"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from flashback.workers.storybook_render.worker import handle_failure, process_one


def _msg(count=1):
    return SimpleNamespace(storybook_id="s", composed_at="t",
                           receipt_handle="rh", receive_count=count)


def test_skip_when_context_missing():
    assert process_one(_msg(), load_context=lambda *a: None,
                       run_render=MagicMock(), mark_complete=MagicMock()) == "skip"


def test_ok_marks_complete():
    ctx = SimpleNamespace(storybook_id="s", person_id="p", collection="childhood")
    mc = MagicMock()
    out = process_one(_msg(), load_context=lambda *a: ctx,
                      run_render=lambda c: (True, 7, True), mark_complete=mc)
    assert out == "ok"
    mc.assert_called_once_with("s", "p", "childhood", True, 7, True)


def test_handle_failure_retries_then_fails():
    mf, ack = MagicMock(), MagicMock()
    assert handle_failure(_msg(1), RuntimeError("x"), max_attempts=3,
                          mark_failed=mf, ack=ack) == "retry"
    assert handle_failure(_msg(3), RuntimeError("x"), max_attempts=3,
                          mark_failed=mf, ack=ack) == "failed"
    mf.assert_called_once()
    ack.assert_called_once_with("rh")
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement the five files per the interfaces above, copying the tribute worker's structure (`_StopSignal`, drain loop, ack-on-ok/skip, redrive on exception, terminal `mark_failed` + ack on final attempt).
- [ ] **Step 4: DB persistence test** (`tests/storybook/test_worker_persistence.py`, patterned on `tests/tribute_video/test_worker.py`'s DB cases): insert a person + storybook row with a valid context dict under `latest_generation_context['storybook']`, assert `load_render_context` returns it, returns None for mismatched `composed_at`; run `mark_complete` inside a LISTEN connection and assert the NOTIFY payload arrives with `pdf_present=True, pages_present=7`; `mark_failed` flips only `generating` rows.
- [ ] **Step 5:** Run all → PASS. **Step 6:** Commit: `feat(storybook): storybook_render worker — curate, assemble, render, presigned upload, NOTIFY`

---

### Task 10: Route rework — POST /storybooks (+ regenerate/edit) on the new pipeline

**Files:**
- Modify: `src/flashback/http/models.py` (request/response models)
- Modify: `src/flashback/http/routes/storybooks.py`
- Modify: `src/flashback/storybook/generation.py` (rewrite the three entry points)
- Modify: `src/flashback/storybook/repository.py` (insert/update carry `collection`; status reset on regen/edit)
- Test: `tests/http/test_storybook_generate.py` (rewrite), `tests/http/test_storybook_regenerate.py` (rewrite)

**Interfaces:**
- Consumes: Tasks 3, 8 (`COLLECTIONS`, `PAGE_COUNT`, `build_context_dict`, `StorybookRenderQueueProducer`, `get_storybook_render_queue`).
- Produces request models:

```python
class StorybookGenerateRequest(BaseModel):
    person_id: UUID
    collection: str
    pdf_put_url: str
    cover_put_url: str
    page_put_urls: list[str]           # exactly PAGE_COUNT entries
    anchor_photo_get_url: str | None = None


class StorybookRegenerateRequest(BaseModel):
    person_id: UUID
    pdf_put_url: str
    cover_put_url: str
    page_put_urls: list[str]
    anchor_photo_get_url: str | None = None


class StorybookEditRequest(StorybookRegenerateRequest):
    instructions: str
    prior_instructions: list[str] = []
```

- Produces `StorybookJobResponse` (rework): `job_id, storybook_id, person_id, collection, status="generating", moments_count, enqueued`.
- Produces generation.py entry points: `generate_storybook(...)` → validate collection slug (400 via `UnknownCollection`), validate `len(page_put_urls) == PAGE_COUNT` (400), fetch person + qualifying moment pool (reuse existing repository fetchers), floor check `len(pool) >= STORYBOOK_MIN_MOMENTS` else raise `StorybookTooThin` → **409** ("Not enough stories yet — keep sharing memories of {name}"); insert row (`status='generating'`, `collection`), `build_context_dict(...)` written to `latest_generation_context['storybook']` **before** `queue.push(...)`; returns result with `enqueued`. `regenerate_storybook` = fresh context on the existing row with `reuse_script=True`, status back to `'generating'`. `edit_storybook` = fresh context with `edit_instructions=[*prior_instructions, instructions]`, `reuse_script=False`.
- **Retires** the `artifact_generation` push, preset plumbing, and tag selection from these flows (`tags` column stops being written; `flashback/storybook/tags.py` no longer imported here).

- [ ] **Step 1: Rewrite the HTTP tests first** (patterned on `tests/http/test_tribute_generate.py` fixtures — mock pool + queue):
  - unknown collection → 400; wrong `page_put_urls` length → 400
  - thin pool (repository fetch mocked below floor) → **409** and detail contains "keep sharing"
  - happy path → 200, response carries `collection`, `status="generating"`, `enqueued=True`; assert the DB write of `latest_generation_context['storybook']` happened before `queue.push` (mock call order), and the SQS payload is trigger-only
  - edit → context contains cumulative `edit_instructions`; regenerate → `reuse_script=True`
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement models + generation.py rewrite + route changes; delete `_resolve_preset_or_400` and artifact-queue Depends from the storybooks router (replaced by `get_storybook_render_queue`).
- [ ] **Step 4:** Run the rewritten tests + full HTTP suite (`python -m pytest tests/http/ -q`) → PASS (except the pre-existing known failures).
- [ ] **Step 5:** Commit: `feat(storybook): rework /storybooks onto the Python render pipeline (collection + presigned URLs, 409 floor)`

---

### Task 11: Contract docs + Node handoff + retirement notes

**Files:**
- Modify: `API.md` (POST /storybooks new shape, regenerate/edit, GET /storybook-collections)
- Modify: `NODE_INTEGRATION.md` (Node mints `pdf + cover + 7 page` PUT URLs + optional anchor-photo GET from `persons.latest_generation_context.reference_s3_key` when `mode='with_reference'`; LISTEN `storybook_render_complete`; write `pdf_url`, `page_urls`, cover `image_url`/`thumbnail_url`; STOP consuming storybook jobs off `artifact_generation`)
- Modify: `CLAUDE.md` §3 (tribute exception paragraph gains the storybook sibling: agent renders storybooks via presigned URLs, Node writes URL columns on NOTIFY)
- Create: `docs/STORYBOOK_PYTHON_NODE_PROMPT.md` (the Node-repo work order, mirroring `docs/CONTRIBUTOR_GENDER_NODE_PROMPT.md` / the tribute handoff format: env `STORYBOOK_RENDER_QUEUE_URL` provisioning, presigned URL minting endpoint changes, LISTEN handler, retirement checklist for the Node storybook renderer)
- Test: none (docs) — but grep-verify no stale references: `grep -rn "artifact_generation" src/flashback/storybook/ src/flashback/http/routes/storybooks.py` returns nothing.

- [ ] **Step 1:** Write all four docs. The NOTIFY payload documented exactly: `{event, storybook_id, person_id, collection, status, pdf_present, pages_present, cover_present}`. The anchor-photo rule documented exactly as decided: **latest `latest_generation_context` is the source of truth** — `mode='with_reference'` → mint GET for its `reference_s3_key`; `mode='no_reference'` → omit (worker builds refs from ground truth).
- [ ] **Step 2:** Run the retirement grep above → empty. Run `python -m pytest tests/ -q` full sweep; confirm only pre-existing known failures.
- [ ] **Step 3:** Commit: `docs(storybook): API + Node contract for Python-owned storybook render; retire Node renderer path`

---

## Self-Review (done at authoring time)

- **Spec coverage:** §4 shared core → Task 1; §5 manifest + GET → Task 3; §6 selection/assembly + floor → Tasks 4, 5, 10; §7 compositor/render → Task 7; §8 worker/queue/config → Tasks 8, 9; §9 migration/context → Tasks 2, 8; §10 routes + retirement + docs → Tasks 10, 11; §11 validation gate → already passed via the spike (recorded in plan header). Session decisions folded in: two-tier tone (Task 3/5), signature images (Task 3/5), age_stage + master refs + identity rule (Tasks 5, 6), lettering verifier (Task 6), anchor-photo-from-latest-context rule (Tasks 8, 9, 11), eligibility floor message "keep sharing memories" (Task 10), blank-panel accounting (Task 7).
- **Placeholder scan:** port steps name exact in-repo source symbols (the spike file) rather than TBDs; all new glue code is written out.
- **Type consistency:** `StorybookRenderContext` field names match `build_context_dict` keys and Task 9/10 consumers; `mark_complete(storybook_id, person_id, collection, pdf_present, pages_present, cover_present)` matches the `process_one` test; `MasterRefs.for_stage` matches Task 7's renderer usage; `PAGE_COUNT` used consistently (models validation, URLs, templates).

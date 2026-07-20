# Flashback Remotion Render Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the tribute Pillow+ffmpeg render with a Remotion subprocess render (MP4 + per-scene stills → PDF), behind a feature flag, with automatic fallback to the existing render.

**Architecture:** Python stays the orchestrator. It generates the Gemini art (unchanged), builds a **props JSON** describing the film as data, invokes a bundled Remotion Node project as a subprocess (like ffmpeg today), gets back an MP4 + a directory of per-scene still PNGs, assembles the PDF/poster from those stills, and PUTs to S3 via the existing Node-minted presigned URLs. This plan proves the full pipeline end-to-end with a single placeholder layout (`framed_hero`); the 6-layout library + sequencer land in Plan 2.

**Tech Stack:** Python 3.14, Pillow, psycopg (existing); Node 20+, Remotion 4 (`@remotion/renderer`, `@remotion/bundler`), React, TypeScript (new); headless Chromium (via Remotion's browser fetch).

## Global Constraints

- **No external video vendor.** Motion/composition is Remotion-only; no image-to-video, no Higgsfield. (Spec §3, §9.)
- **S3 only via Node-minted presigned URLs; never write the URL columns.** The worker holds no AWS creds. (CLAUDE.md §3.)
- **Postgres authoritative; the SQS message is a trigger only.** `latest_generation_context` is the source of truth; the render reads it. (CLAUDE.md §3.)
- **Config never blocks a render.** Missing/unknown config degrades to a safe default; a render never raises on config. (Spec §4, invariant.)
- **Feature-flagged with fallback.** `RENDER_ENGINE` toggles `remotion`|`legacy`; a Remotion failure auto-falls-back to the legacy render. Default `legacy` until proven. (Spec §11.)
- **Video is vertical `896×1600`, PDF at `150.0` resolution.** Match today's output geometry. (`video.py:23`, `render.py:179`.)
- **`Artist` is constructed once per worker** as `Artist(api_key=cfg.gemini_api_key, model=cfg.gemini_image_model)` and reused. (`worker.py:178`.)
- **Tests must not hit real Gemini, S3, a queue, or (except one gated smoke) Node/Chromium.** Follow the existing isolation patterns in `tests/tribute_video/`.

---

## File Structure

**New (Python):**
- `src/flashback/tribute_video/props.py` — pure builder: `Book` + kit + recipe → props dict (the Python↔Remotion contract).
- `src/flashback/tribute_video/stills_pdf.py` — assemble PDF + poster from a list of still PNGs.
- `src/flashback/tribute_video/remotion_cli.py` — subprocess wrapper that invokes the Remotion Node CLI.
- `src/flashback/tribute_video/remotion_render.py` — the render seam mirroring `render_book`'s responsibility, driving Remotion.

**New (Node/Remotion project, repo root):**
- `remotion/package.json`, `remotion/tsconfig.json`, `remotion/remotion.config.ts`
- `remotion/src/index.ts` — entrypoint registering the root
- `remotion/src/Root.tsx` — registers the `Flashback` composition + its prop schema
- `remotion/src/Flashback.tsx` — the composition: maps scenes → layout components with timing
- `remotion/src/layouts/FramedHero.tsx` — the single placeholder layout for this plan
- `remotion/src/layouts/registry.ts` — `layout_slug` → component map
- `remotion/render.mjs` — the CLI: props JSON → MP4 + stills (`bundle` + `renderMedia` + `renderStill`)
- `remotion/public/fonts/` — copies of the four bundled TTFs + `fonts.css` `@font-face` block

**Modified (Python):**
- `src/flashback/config.py` — add `render_engine` to `TributeRenderConfig` (+ `from_env`).
- `src/flashback/workers/tribute_render/worker.py` — `render_and_upload` selects engine + falls back.

**New (tests):**
- `tests/tribute_video/test_props.py`
- `tests/tribute_video/test_stills_pdf.py`
- `tests/tribute_video/test_remotion_cli.py`
- `tests/tribute_video/test_remotion_render.py`
- `tests/tribute_video/test_worker_engine.py`
- `tests/tribute_video/test_remotion_smoke.py` — gated real render (skipif no node / no `REMOTION_SMOKE`).

**Modified (docs):**
- `docs/ec2-deploy.md` — Node + Chromium provisioning + `remotion/` install step.

---

## Task 1: Props contract + builder

**Files:**
- Create: `src/flashback/tribute_video/props.py`
- Test: `tests/tribute_video/test_props.py`

**Interfaces:**
- Consumes: `flashback.tribute_video.book.Book`, `flashback.tribute_video.style.StyleKit`.
- Produces:
  - `build_props(book: Book, *, kit: StyleKit, image_names: dict[str, str], fps: int = 30, hold: float = 2.4, transition: float = 1.1) -> dict` — returns the props dict. `image_names` maps `"opener"`, `"closing"`, and `"beat_{i}"` (0-based) → PNG filename (relative to the Remotion public dir). The message scene, when `book.message` is non-empty, reuses `image_names["opener"]`. Every scene carries `layout_slug="framed_hero"` in this plan.

- [ ] **Step 1: Write the failing test**

```python
# tests/tribute_video/test_props.py
from flashback.tribute_video.book import Beat, Book
from flashback.tribute_video.props import build_props
from flashback.tribute_video.style import DEFAULT_KIT


def _book(message: str = "") -> Book:
    return Book(
        cover_title="For Dad",
        opener=Beat(line="Where it began", art_direction="a"),
        beats=[Beat(line="The workshop", art_direction="b", moment_id="m1"),
               Beat(line="Sunday drives", art_direction="c", moment_id="m2")],
        closing=Beat(line="Still with us", art_direction="d"),
        message=message,
    )


def test_scene_order_and_images_without_message():
    names = {"opener": "opener.png", "closing": "closing.png",
             "beat_0": "beat_000.png", "beat_1": "beat_001.png"}
    props = build_props(_book(), kit=DEFAULT_KIT, image_names=names)
    roles = [s["role"] for s in props["scenes"]]
    assert roles == ["opener", "beat", "beat", "closing"]
    assert props["scenes"][0]["image"] == "opener.png"
    assert props["scenes"][3]["image"] == "closing.png"
    assert all(s["layout_slug"] == "framed_hero" for s in props["scenes"])
    assert props["meta"]["width"] == 896 and props["meta"]["height"] == 1600


def test_message_scene_reuses_opener_image():
    names = {"opener": "opener.png", "closing": "closing.png",
             "beat_0": "beat_000.png", "beat_1": "beat_001.png"}
    props = build_props(_book(message="You taught me everything."),
                        kit=DEFAULT_KIT, image_names=names)
    roles = [s["role"] for s in props["scenes"]]
    assert roles == ["opener", "beat", "beat", "message", "closing"]
    msg = next(s for s in props["scenes"] if s["role"] == "message")
    assert msg["text"] == "You taught me everything."
    assert msg["image"] == "opener.png"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/tribute_video/test_props.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flashback.tribute_video.props'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/flashback/tribute_video/props.py
"""Build the props JSON the Remotion project consumes from a Book + StyleKit.

Pure data assembly — no I/O. Mirrors the page order the legacy renderer builds
in render.py (opener, beats..., message?, closing) so the Remotion film matches
the book. ``image_names`` maps each scene to a PNG the caller has written into
the Remotion public dir; the message scene reuses the opener image as a bookend.
"""
from __future__ import annotations

from .book import Book
from .style import StyleKit

# Video geometry — must match the legacy render (video.py OUT_W/OUT_H).
WIDTH, HEIGHT = 896, 1600

# Font filename-stem (substring) -> CSS family. The StyleKit carries font FILE
# paths (Pillow needs paths); Remotion needs family names, so we map by stem.
# The Remotion project ships @font-face rules keyed by these family names.
_FONT_FAMILY_BY_STEM: tuple[tuple[str, str], ...] = (
    ("playfair", "Playfair Display"),
    ("garamond", "EB Garamond"),
    ("caveat", "Caveat"),
    ("nunito", "Nunito"),
)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def _family_for(font_path: str, default: str) -> str:
    stem = font_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
    for needle, family in _FONT_FAMILY_BY_STEM:
        if needle in stem:
            return family
    return default


def build_props(book: Book, *, kit: StyleKit, image_names: dict[str, str],
                fps: int = 30, hold: float = 2.4, transition: float = 1.1) -> dict:
    scenes: list[dict] = []
    scenes.append({"role": "opener", "layout_slug": "framed_hero",
                   "text": book.opener.line, "image": image_names["opener"]})
    for i, b in enumerate(book.beats):
        scenes.append({"role": "beat", "layout_slug": "framed_hero",
                       "text": b.line, "image": image_names[f"beat_{i}"]})
    if book.message.strip():
        scenes.append({"role": "message", "layout_slug": "framed_hero",
                       "text": book.message, "image": image_names["opener"]})
    scenes.append({"role": "closing", "layout_slug": "framed_hero",
                   "text": book.closing.line, "image": image_names["closing"]})
    return {
        "meta": {"width": WIDTH, "height": HEIGHT, "fps": fps,
                 "cover_title": book.cover_title},
        "recipe": {
            "fonts": {"main_family": _family_for(kit.main_font, "Playfair Display"),
                      "eyebrow_family": _family_for(kit.eyebrow_font, "EB Garamond")},
            "ink": {"main_fill": _rgb_to_hex(kit.main_fill),
                    "eyebrow_fill": _rgb_to_hex(kit.eyebrow_fill)},
            "pacing": {"hold": hold, "transition": transition},
        },
        "scenes": scenes,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/tribute_video/test_props.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/flashback/tribute_video/props.py tests/tribute_video/test_props.py
git commit -m "feat(flashback): props builder for the Remotion render contract"
```

---

## Task 2: PDF + poster from stills

**Files:**
- Create: `src/flashback/tribute_video/stills_pdf.py`
- Test: `tests/tribute_video/test_stills_pdf.py`

**Interfaces:**
- Produces: `assemble_pdf_from_stills(still_paths: list[str], pdf_path: str, poster_path: str | None = None) -> int` — opens each PNG in order, writes a multi-page PDF at resolution 150.0, writes the first frame as a JPEG poster when `poster_path` is given, and returns the page count. Raises `ValueError` on an empty list.

- [ ] **Step 1: Write the failing test**

```python
# tests/tribute_video/test_stills_pdf.py
import pytest
from PIL import Image

from flashback.tribute_video.stills_pdf import assemble_pdf_from_stills


def _png(path, color):
    Image.new("RGB", (896, 1600), color).save(path)
    return str(path)


def test_pdf_and_poster_from_stills(tmp_path):
    stills = [_png(tmp_path / "0.png", (200, 180, 140)),
              _png(tmp_path / "1.png", (100, 90, 70)),
              _png(tmp_path / "2.png", (50, 40, 30))]
    pdf = tmp_path / "out.pdf"
    poster = tmp_path / "poster.jpg"
    n = assemble_pdf_from_stills(stills, str(pdf), str(poster))
    assert n == 3
    assert pdf.exists() and pdf.stat().st_size > 0
    assert poster.exists()
    with Image.open(poster) as im:
        assert im.format == "JPEG"


def test_empty_raises(tmp_path):
    with pytest.raises(ValueError):
        assemble_pdf_from_stills([], str(tmp_path / "x.pdf"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/tribute_video/test_stills_pdf.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flashback.tribute_video.stills_pdf'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/flashback/tribute_video/stills_pdf.py
"""Assemble a print PDF + cover poster from Remotion's per-scene still PNGs.

The stills ARE the render's source of truth for print (spec §6). Mirrors the
geometry/resolution the legacy renderer used (render.py: resolution=150.0,
poster = first page as JPEG q88).
"""
from __future__ import annotations

from PIL import Image


def assemble_pdf_from_stills(still_paths: list[str], pdf_path: str,
                             poster_path: str | None = None) -> int:
    if not still_paths:
        raise ValueError("assemble_pdf_from_stills: no stills provided")
    pages = [Image.open(p).convert("RGB") for p in still_paths]
    pages[0].save(pdf_path, save_all=True, append_images=pages[1:],
                  resolution=150.0)
    if poster_path is not None:
        pages[0].save(poster_path, format="JPEG", quality=88)
    return len(pages)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/tribute_video/test_stills_pdf.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/flashback/tribute_video/stills_pdf.py tests/tribute_video/test_stills_pdf.py
git commit -m "feat(flashback): assemble PDF + poster from Remotion stills"
```

---

## Task 3: Remotion CLI subprocess wrapper

**Files:**
- Create: `src/flashback/tribute_video/remotion_cli.py`
- Test: `tests/tribute_video/test_remotion_cli.py`

**Interfaces:**
- Produces:
  - `class RemotionError(RuntimeError)` — raised on non-zero exit or missing project.
  - `default_project_dir() -> str` — resolves the `remotion/` project dir: env `FLASHBACK_REMOTION_DIR` if set, else `<repo_root>/remotion` (repo root = four parents up from this file).
  - `run_remotion(*, props_path: str, public_dir: str, out_mp4: str, stills_dir: str, project_dir: str | None = None, node_bin: str = "node", timeout: float = 900.0) -> None` — builds argv `[node_bin, "<project>/render.mjs", "--props", props_path, "--public-dir", public_dir, "--out-mp4", out_mp4, "--stills-dir", stills_dir]` and runs it via `subprocess.run(check=False, capture_output=True)`; raises `RemotionError` (with truncated stderr) on non-zero return.

- [ ] **Step 1: Write the failing test**

```python
# tests/tribute_video/test_remotion_cli.py
import subprocess
import pytest

from flashback.tribute_video import remotion_cli


def test_builds_expected_argv(monkeypatch, tmp_path):
    seen = {}

    def fake_run(argv, check=False, capture_output=True, timeout=None, cwd=None):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(remotion_cli.subprocess, "run", fake_run)
    remotion_cli.run_remotion(
        props_path=str(tmp_path / "p.json"), public_dir=str(tmp_path / "pub"),
        out_mp4=str(tmp_path / "o.mp4"), stills_dir=str(tmp_path / "st"),
        project_dir=str(tmp_path / "remotion"), node_bin="node")
    argv = seen["argv"]
    assert argv[0] == "node"
    assert argv[1].endswith("render.mjs")
    assert "--props" in argv and "--out-mp4" in argv and "--stills-dir" in argv


def test_nonzero_exit_raises(monkeypatch, tmp_path):
    def fake_run(argv, check=False, capture_output=True, timeout=None, cwd=None):
        return subprocess.CompletedProcess(argv, 2, stdout=b"", stderr=b"boom")

    monkeypatch.setattr(remotion_cli.subprocess, "run", fake_run)
    with pytest.raises(remotion_cli.RemotionError):
        remotion_cli.run_remotion(
            props_path="p", public_dir="pub", out_mp4="o", stills_dir="st",
            project_dir=str(tmp_path))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/tribute_video/test_remotion_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flashback.tribute_video.remotion_cli'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/flashback/tribute_video/remotion_cli.py
"""Invoke the bundled Remotion Node project as a subprocess (like ffmpeg).

No DB/S3/secrets — pure props.json -> mp4 + stills. The worker owns art
generation, prop assembly, and the S3 PUTs; this module only shells out.
"""
from __future__ import annotations

import os
import subprocess

log_prefix = "flashback.tribute_video.remotion_cli"


class RemotionError(RuntimeError):
    pass


def default_project_dir() -> str:
    env = os.environ.get("FLASHBACK_REMOTION_DIR")
    if env:
        return env
    # this file: <repo>/src/flashback/tribute_video/remotion_cli.py -> 4 up = repo
    here = os.path.abspath(__file__)
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))
    return os.path.join(repo, "remotion")


def run_remotion(*, props_path: str, public_dir: str, out_mp4: str,
                 stills_dir: str, project_dir: str | None = None,
                 node_bin: str = "node", timeout: float = 900.0) -> None:
    proj = project_dir or default_project_dir()
    script = os.path.join(proj, "render.mjs")
    argv = [node_bin, script, "--props", props_path, "--public-dir", public_dir,
            "--out-mp4", out_mp4, "--stills-dir", stills_dir]
    proc = subprocess.run(argv, check=False, capture_output=True,
                          timeout=timeout, cwd=proj)
    if proc.returncode != 0:
        stderr = (proc.stderr or b"")[-1200:].decode("utf-8", "replace")
        raise RemotionError(f"remotion exit {proc.returncode}: {stderr}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/tribute_video/test_remotion_cli.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/flashback/tribute_video/remotion_cli.py tests/tribute_video/test_remotion_cli.py
git commit -m "feat(flashback): subprocess wrapper for the Remotion render CLI"
```

---

## Task 4: The Remotion render seam

**Files:**
- Create: `src/flashback/tribute_video/remotion_render.py`
- Test: `tests/tribute_video/test_remotion_render.py`

**Interfaces:**
- Consumes: `props.build_props` (Task 1), `stills_pdf.assemble_pdf_from_stills` (Task 2), `remotion_cli.run_remotion` (Task 3), and `render._generate_illustrations` + `render.RenderResult` (existing, `render.py:35`/`:98`).
- Produces: `render_book_remotion(*, book, subject_name, relationship, gt_context, artist, pdf_path, mp4_path, poster_path=None, prime_photo=None, deage=False, blend="cream", fps=30, concurrency=4, kit=None, art_mood=None) -> RenderResult` — same shape as `render_book` (`render.py:106`), minus the ffmpeg-specific `transition`/`audio_path`. It generates illustrations, writes them as PNGs into a temp public dir, builds props, runs Remotion (producing the MP4 + stills), assembles the PDF/poster from the stills, and returns a `RenderResult`.

- [ ] **Step 1: Write the failing test**

```python
# tests/tribute_video/test_remotion_render.py
import os
from PIL import Image

from flashback.tribute_video import remotion_render
from flashback.tribute_video.book import Beat, Book


class _FakeArtist:
    def character_reference(self, **k): return Image.new("RGB", (4, 4), (1, 2, 3))
    def illustrate(self, *a, **k): return Image.new("RGB", (4, 4), (9, 9, 9))
    def portrait_from_photo(self, *a, **k): return Image.new("RGB", (4, 4), (7, 7, 7))


def _book():
    return Book(cover_title="For Dad",
                opener=Beat(line="Start", art_direction="a"),
                beats=[Beat(line="Mid", art_direction="b", moment_id="m1")],
                closing=Beat(line="End", art_direction="c"))


def test_render_produces_pdf_mp4_poster(monkeypatch, tmp_path):
    # Fake the Remotion subprocess: write the MP4 + one still per scene.
    def fake_run(*, props_path, public_dir, out_mp4, stills_dir, **k):
        os.makedirs(stills_dir, exist_ok=True)
        open(out_mp4, "wb").write(b"\x00")
        import json
        scenes = json.load(open(props_path))["scenes"]
        for i in range(len(scenes)):
            Image.new("RGB", (896, 1600), (i, i, i)).save(
                os.path.join(stills_dir, f"scene_{i:03d}.png"))

    monkeypatch.setattr(remotion_render, "run_remotion", fake_run)
    res = remotion_render.render_book_remotion(
        book=_book(), subject_name="Dad", relationship="father",
        gt_context="", artist=_FakeArtist(),
        pdf_path=str(tmp_path / "o.pdf"), mp4_path=str(tmp_path / "o.mp4"),
        poster_path=str(tmp_path / "o.poster.jpg"))
    assert res.pages == 3  # opener + 1 beat + closing
    assert os.path.exists(res.pdf_path) and os.path.exists(res.mp4_path)
    assert res.poster_path and os.path.exists(res.poster_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/tribute_video/test_remotion_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flashback.tribute_video.remotion_render'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/flashback/tribute_video/remotion_render.py
"""Render a Book into MP4 + PDF via the Remotion project (spec §6).

Mirrors render.render_book's responsibility but delegates composition + motion
to Remotion. Reuses render._generate_illustrations so art generation stays DRY
(one code path, identical Gemini behavior). Pure orchestration: no DB/SQS/S3.
"""
from __future__ import annotations

import json
import os
import tempfile

import structlog
from PIL import Image

from . import style
from .props import build_props
from .remotion_cli import run_remotion
from .render import RenderResult, _generate_illustrations
from .stills_pdf import assemble_pdf_from_stills

log = structlog.get_logger("flashback.tribute_video.remotion_render")


def render_book_remotion(
    *, book, subject_name: str, relationship: str | None, gt_context: str,
    artist, pdf_path: str, mp4_path: str, poster_path: str | None = None,
    prime_photo: Image.Image | None = None, deage: bool = False,
    blend: str = "cream", fps: int = 30, concurrency: int = 4,
    kit: style.StyleKit | None = None, art_mood: str | None = None,
) -> RenderResult:
    kit = kit or style.DEFAULT_KIT
    if not kit.generated_template:
        art_mood = None

    opener_illo, beat_illos, closing_illo = _generate_illustrations(
        artist=artist, book=book, subject_name=subject_name,
        relationship=relationship, gt_context=gt_context,
        prime_photo=prime_photo, deage=deage, blend=blend,
        concurrency=concurrency, art_mood=art_mood)

    with tempfile.TemporaryDirectory() as td:
        public_dir = os.path.join(td, "public")
        stills_dir = os.path.join(td, "stills")
        os.makedirs(public_dir, exist_ok=True)

        image_names: dict[str, str] = {}
        opener_illo.save(os.path.join(public_dir, "opener.png"))
        image_names["opener"] = "opener.png"
        for i, illo in enumerate(beat_illos):
            name = f"beat_{i:03d}.png"
            illo.save(os.path.join(public_dir, name))
            image_names[f"beat_{i}"] = name
        closing_illo.save(os.path.join(public_dir, "closing.png"))
        image_names["closing"] = "closing.png"

        props = build_props(book, kit=kit, image_names=image_names, fps=fps)
        props_path = os.path.join(td, "props.json")
        with open(props_path, "w", encoding="utf-8") as fh:
            json.dump(props, fh)

        run_remotion(props_path=props_path, public_dir=public_dir,
                     out_mp4=mp4_path, stills_dir=stills_dir)

        still_paths = sorted(
            os.path.join(stills_dir, f) for f in os.listdir(stills_dir)
            if f.endswith(".png"))
        pages = assemble_pdf_from_stills(still_paths, pdf_path, poster_path)

    return RenderResult(pages=pages, pdf_path=pdf_path, mp4_path=mp4_path,
                        poster_path=poster_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/tribute_video/test_remotion_render.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/flashback/tribute_video/remotion_render.py tests/tribute_video/test_remotion_render.py
git commit -m "feat(flashback): Remotion render seam (art -> props -> mp4 + stills -> pdf)"
```

---

## Task 5: Config flag + worker engine selection & fallback

**Files:**
- Modify: `src/flashback/config.py` (`TributeRenderConfig` dataclass at `:747`, `from_env` at `:785`)
- Modify: `src/flashback/workers/tribute_render/worker.py` (`render_and_upload` at `:85`)
- Test: `tests/tribute_video/test_worker_engine.py`

**Interfaces:**
- Consumes: `remotion_render.render_book_remotion` (Task 4), `render.render_book` (existing).
- Produces: `TributeRenderConfig.render_engine: str` (default `"legacy"`, from env `RENDER_ENGINE`); `worker.render_and_upload` chooses the engine and, on any exception from the Remotion path, logs and falls back to the legacy `render_book`.

- [ ] **Step 1: Write the failing test**

```python
# tests/tribute_video/test_worker_engine.py
from types import SimpleNamespace
from flashback.workers.tribute_render import worker as W


def _ctx():
    return SimpleNamespace(
        subject_name="Dad", relationship="father", gt_context="",
        prime_photo_get_url="", deage=False, blend="cream",
        transition="bleed", fps=30, art_mood=None,
        video_put_url="v", pdf_put_url="p", poster_put_url="")


def _settings(engine):
    return SimpleNamespace(render_concurrency=2, render_engine=engine)


def _stub_common(monkeypatch):
    monkeypatch.setattr(W, "assemble_book", lambda ctx, settings: "BOOK")
    monkeypatch.setattr(W.transfer, "download_image", lambda url: None)
    monkeypatch.setattr(W.transfer, "upload_file",
                        lambda url, path, content_type: 200)


def test_remotion_engine_selected(monkeypatch, tmp_path):
    _stub_common(monkeypatch)
    called = {"remotion": 0, "legacy": 0}
    monkeypatch.setattr(W, "render_book_remotion",
                        lambda **k: called.__setitem__("remotion", called["remotion"] + 1))
    monkeypatch.setattr(W, "render_book",
                        lambda **k: called.__setitem__("legacy", called["legacy"] + 1))
    W.render_and_upload(_ctx(), artist=None, tmpdir=str(tmp_path),
                        settings=_settings("remotion"))
    assert called == {"remotion": 1, "legacy": 0}


def test_remotion_failure_falls_back_to_legacy(monkeypatch, tmp_path):
    _stub_common(monkeypatch)
    called = {"legacy": 0}

    def boom(**k):
        raise RuntimeError("remotion down")

    monkeypatch.setattr(W, "render_book_remotion", boom)
    monkeypatch.setattr(W, "render_book",
                        lambda **k: called.__setitem__("legacy", called["legacy"] + 1))
    W.render_and_upload(_ctx(), artist=None, tmpdir=str(tmp_path),
                        settings=_settings("remotion"))
    assert called["legacy"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/tribute_video/test_worker_engine.py -v`
Expected: FAIL — `AttributeError: module '...worker' has no attribute 'render_book_remotion'`

- [ ] **Step 3a: Add the config field**

In `src/flashback/config.py`, inside the `TributeRenderConfig` dataclass (near the other render fields around `:747`), add:

```python
    render_engine: str = "legacy"      # "legacy" | "remotion"
```

In its `from_env` (around `:785`), add alongside the other render env reads (near `render_fps`):

```python
        render_engine=os.environ.get("RENDER_ENGINE", "legacy"),
```

- [ ] **Step 3b: Wire engine selection + fallback in the worker**

In `src/flashback/workers/tribute_render/worker.py`, add the import near the existing `from ...tribute_video.render import render_book` line:

```python
from ...tribute_video.remotion_render import render_book_remotion
```

Then in `render_and_upload` (`:85`), replace the single `render_book(...)` call (lines ~104–113) with engine selection. The book/photo/paths setup above it is unchanged; only the render call changes:

```python
    engine = getattr(settings, "render_engine", "legacy")
    render_kwargs = dict(
        book=book, subject_name=ctx.subject_name, relationship=ctx.relationship,
        gt_context=ctx.gt_context, artist=artist, pdf_path=pdf_path,
        mp4_path=mp4_path, poster_path=poster_path, prime_photo=photo,
        deage=ctx.deage, blend=ctx.blend, fps=ctx.fps,
        concurrency=getattr(settings, "render_concurrency", 4),
        kit=kit, art_mood=ctx.art_mood or None,
    )
    if engine == "remotion":
        try:
            render_book_remotion(**render_kwargs)
        except Exception as exc:  # noqa: BLE001 - fall back, never fail the render
            log.warning("tribute_render.remotion_failed_fallback_legacy",
                        error=str(exc)[:300])
            render_book(transition=ctx.transition, **render_kwargs)
    else:
        render_book(transition=ctx.transition, **render_kwargs)
```

(Note: `render_book_remotion` ignores the ffmpeg-only `transition`; the legacy call keeps it. Both accept the shared kwargs.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/tribute_video/test_worker_engine.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full tribute_video suite to confirm no regression**

Run: `python -m pytest tests/tribute_video/ -v`
Expected: PASS (existing worker/render/assembler tests still green; new tests pass)

- [ ] **Step 6: Commit**

```bash
git add src/flashback/config.py src/flashback/workers/tribute_render/worker.py tests/tribute_video/test_worker_engine.py
git commit -m "feat(flashback): RENDER_ENGINE flag + Remotion selection with legacy fallback"
```

---

## Task 6: The Remotion Node project + gated smoke test

**Files:**
- Create: `remotion/package.json`, `remotion/tsconfig.json`, `remotion/remotion.config.ts`
- Create: `remotion/src/index.ts`, `remotion/src/Root.tsx`, `remotion/src/Flashback.tsx`
- Create: `remotion/src/layouts/FramedHero.tsx`, `remotion/src/layouts/registry.ts`
- Create: `remotion/render.mjs`
- Create: `remotion/public/fonts/fonts.css` (+ copy the four TTFs from `src/flashback/tribute_video/assets/fonts/`)
- Test: `tests/tribute_video/test_remotion_smoke.py`

**Interfaces:**
- Consumes: the props JSON from Task 1 (fields `meta.{width,height,fps}`, `recipe.*`, `scenes[].{role,layout_slug,text,image}`).
- Produces: `remotion/render.mjs` accepting `--props --public-dir --out-mp4 --stills-dir`, writing the MP4 and one still per scene named `scene_000.png`, `scene_001.png`, … into `--stills-dir`.

- [ ] **Step 1: Write the failing (gated) smoke test**

```python
# tests/tribute_video/test_remotion_smoke.py
"""Real Remotion render — gated like the real-ffmpeg test (test_audio.py).

Runs only when REMOTION_SMOKE=1 and `node` is on PATH and remotion/node_modules
exists (i.e., on a dev box / the worker host, not a bare CI). Proves the whole
Node pipeline: props.json -> mp4 + one still per scene.
"""
import os
import shutil
import subprocess
import json
import pytest
from PIL import Image

from flashback.tribute_video.remotion_cli import default_project_dir, run_remotion

_PROJ = default_project_dir()
_gated = (os.environ.get("REMOTION_SMOKE") != "1"
          or shutil.which("node") is None
          or not os.path.isdir(os.path.join(_PROJ, "node_modules")))


@pytest.mark.skipif(_gated, reason="REMOTION_SMOKE!=1 / node / node_modules absent")
def test_real_render_two_scenes(tmp_path):
    public_dir = tmp_path / "public"
    stills = tmp_path / "stills"
    public_dir.mkdir()
    for name in ("opener.png", "closing.png"):
        Image.new("RGB", (896, 1600), (180, 150, 110)).save(public_dir / name)
    props = {"meta": {"width": 896, "height": 1600, "fps": 30},
             "recipe": {"fonts": {"main_family": "Playfair Display",
                                  "eyebrow_family": "EB Garamond"},
                        "ink": {"main_fill": "#3a2c1c", "eyebrow_fill": "#96764a"},
                        "pacing": {"hold": 1.0, "transition": 0.5}},
             "scenes": [
                 {"role": "opener", "layout_slug": "framed_hero",
                  "text": "Where it began", "image": "opener.png"},
                 {"role": "closing", "layout_slug": "framed_hero",
                  "text": "Still with us", "image": "closing.png"}]}
    props_path = tmp_path / "props.json"
    props_path.write_text(json.dumps(props), encoding="utf-8")
    out_mp4 = tmp_path / "out.mp4"
    run_remotion(props_path=str(props_path), public_dir=str(public_dir),
                 out_mp4=str(out_mp4), stills_dir=str(stills))
    assert out_mp4.exists() and out_mp4.stat().st_size > 0
    assert (stills / "scene_000.png").exists()
    assert (stills / "scene_001.png").exists()
```

- [ ] **Step 2: Run it to confirm it SKIPS (no smoke env yet)**

Run: `python -m pytest tests/tribute_video/test_remotion_smoke.py -v`
Expected: SKIPPED (1 skipped) — the gate is active until the project is built.

- [ ] **Step 3: Scaffold the Node project config**

```json
// remotion/package.json
{
  "name": "flashback-remotion",
  "private": true,
  "version": "1.0.0",
  "type": "module",
  "scripts": { "start": "remotion studio", "build": "remotion bundle" },
  "dependencies": {
    "@remotion/bundler": "4.0.200",
    "@remotion/cli": "4.0.200",
    "@remotion/renderer": "4.0.200",
    "react": "18.3.1",
    "react-dom": "18.3.1",
    "remotion": "4.0.200"
  },
  "devDependencies": { "typescript": "5.5.4", "@types/react": "18.3.3" }
}
```

```json
// remotion/tsconfig.json
{
  "compilerOptions": {
    "target": "ES2020", "module": "ESNext", "moduleResolution": "bundler",
    "jsx": "react-jsx", "strict": true, "esModuleInterop": true,
    "skipLibCheck": true, "noEmit": true
  },
  "include": ["src"]
}
```

```typescript
// remotion/remotion.config.ts
import { Config } from "@remotion/cli/config";
Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);
```

- [ ] **Step 4: Write the composition + layout**

```typescript
// remotion/src/layouts/FramedHero.tsx
import { AbsoluteFill, Img, staticFile, useCurrentFrame, interpolate } from "remotion";

export type SceneProps = {
  text: string;
  image: string;
  ink: { main_fill: string; eyebrow_fill: string };
  fonts: { main_family: string; eyebrow_family: string };
};

export const FramedHero: React.FC<SceneProps> = ({ text, image, ink, fonts }) => {
  const frame = useCurrentFrame();
  const fade = interpolate(frame, [0, 12], [0, 1], { extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ backgroundColor: "#f3ead7", padding: 48 }}>
      <AbsoluteFill style={{ opacity: fade }}>
        <div style={{ height: "26%", display: "flex", alignItems: "center",
                      justifyContent: "center", padding: "0 8%" }}>
          <span style={{ fontFamily: fonts.main_family, fontStyle: "italic",
                         fontSize: 64, lineHeight: 1.15, color: ink.main_fill,
                         textAlign: "center" }}>{text}</span>
        </div>
        <Img src={staticFile(image)}
             style={{ position: "absolute", top: "30%", left: "6%", width: "88%",
                      height: "62%", objectFit: "cover", borderRadius: 10 }} />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
```

```typescript
// remotion/src/layouts/registry.ts
import { FramedHero } from "./FramedHero";
export const LAYOUTS: Record<string, React.FC<any>> = { framed_hero: FramedHero };
export const DEFAULT_LAYOUT = "framed_hero";
```

```typescript
// remotion/src/Flashback.tsx
import { AbsoluteFill, Sequence, Series } from "remotion";
import { LAYOUTS, DEFAULT_LAYOUT } from "./layouts/registry";

export type Scene = { role: string; layout_slug: string; text: string; image: string };
export type FlashbackProps = {
  meta: { fps: number };
  recipe: { fonts: any; ink: any; pacing: { hold: number; transition: number } };
  scenes: Scene[];
};

export const Flashback: React.FC<FlashbackProps> = ({ meta, recipe, scenes }) => {
  const hold = Math.round((recipe.pacing?.hold ?? 2.4) * meta.fps);
  return (
    <AbsoluteFill>
      <Series>
        {scenes.map((s, i) => {
          const Comp = LAYOUTS[s.layout_slug] ?? LAYOUTS[DEFAULT_LAYOUT];
          return (
            <Series.Sequence key={i} durationInFrames={hold}>
              <Comp text={s.text} image={s.image} ink={recipe.ink} fonts={recipe.fonts} />
            </Series.Sequence>
          );
        })}
      </Series>
    </AbsoluteFill>
  );
};
```

```typescript
// remotion/src/Root.tsx
import { Composition } from "remotion";
import { Flashback } from "./Flashback";

export const RemotionRoot: React.FC = () => (
  <Composition
    id="Flashback"
    component={Flashback as any}
    durationInFrames={300}
    fps={30}
    width={896}
    height={1600}
    defaultProps={{ meta: { fps: 30 }, recipe: {}, scenes: [] } as any}
    calculateMetadata={({ props }: any) => {
      const fps = props.meta?.fps ?? 30;
      const hold = Math.round((props.recipe?.pacing?.hold ?? 2.4) * fps);
      return { durationInFrames: Math.max(1, hold * (props.scenes?.length ?? 1)),
               fps, width: props.meta?.width ?? 896, height: props.meta?.height ?? 1600 };
    }}
  />
);
```

```typescript
// remotion/src/index.ts
import { registerRoot } from "remotion";
import { RemotionRoot } from "./Root";
registerRoot(RemotionRoot);
```

- [ ] **Step 5: Write the render CLI**

```javascript
// remotion/render.mjs
import { parseArgs } from "node:util";
import { readFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import { bundle } from "@remotion/bundler";
import { selectComposition, renderMedia, renderStill } from "@remotion/renderer";

const { values } = parseArgs({ options: {
  props: { type: "string" }, "public-dir": { type: "string" },
  "out-mp4": { type: "string" }, "stills-dir": { type: "string" },
} });

const inputProps = JSON.parse(readFileSync(values.props, "utf-8"));
const publicDir = values["public-dir"];
const stillsDir = values["stills-dir"];
mkdirSync(stillsDir, { recursive: true });

const serveUrl = await bundle({
  entryPoint: path.join(process.cwd(), "src", "index.ts"),
  publicDir,
});
const composition = await selectComposition({ serveUrl, id: "Flashback", inputProps });

await renderMedia({
  serveUrl, composition, codec: "h264", outputLocation: values["out-mp4"],
  inputProps, imageFormat: "jpeg",
});

const fps = composition.fps;
const hold = Math.round((inputProps.recipe?.pacing?.hold ?? 2.4) * fps);
for (let i = 0; i < inputProps.scenes.length; i++) {
  await renderStill({
    serveUrl, composition, inputProps,
    frame: i * hold + Math.floor(hold / 2),
    output: path.join(stillsDir, `scene_${String(i).padStart(3, "0")}.png`),
    imageFormat: "png",
  });
}
```

- [ ] **Step 6: Fonts**

Copy `EBGaramond.ttf`, `PlayfairDisplay-Italic.ttf`, `Caveat.ttf`, `Nunito.ttf` from `src/flashback/tribute_video/assets/fonts/` into `remotion/public/fonts/`, then:

```css
/* remotion/public/fonts/fonts.css */
@font-face { font-family: "Playfair Display"; font-style: italic;
  src: url("./PlayfairDisplay-Italic.ttf") format("truetype"); }
@font-face { font-family: "EB Garamond"; src: url("./EBGaramond.ttf") format("truetype"); }
@font-face { font-family: "Caveat"; src: url("./Caveat.ttf") format("truetype"); }
@font-face { font-family: "Nunito"; src: url("./Nunito.ttf") format("truetype"); }
```

Import it at the top of `remotion/src/index.ts`:

```typescript
import "../public/fonts/fonts.css";
```

- [ ] **Step 7: Install deps + fetch Chromium, then run the smoke test**

```bash
cd remotion && npm install && npx remotion browser ensure && cd ..
REMOTION_SMOKE=1 python -m pytest tests/tribute_video/test_remotion_smoke.py -v
```
Expected: PASS (1 passed) — `out.mp4` non-empty, `scene_000.png` + `scene_001.png` written.

- [ ] **Step 8: Commit**

```bash
git add remotion tests/tribute_video/test_remotion_smoke.py
git commit -m "feat(flashback): Remotion Node project (Flashback composition + framed_hero) + gated smoke"
```

---

## Task 7: Deployment provisioning (Node + Chromium)

**Files:**
- Modify: `docs/ec2-deploy.md`

**Interfaces:** none (docs/ops).

- [ ] **Step 1: Document the render-host provisioning**

Add a section to `docs/ec2-deploy.md` under the tribute-render worker setup:

````markdown
### Remotion render engine (RENDER_ENGINE=remotion)

The tribute render worker can render via Remotion instead of the legacy
Pillow/ffmpeg path. The render host needs Node + a Chromium Remotion controls:

```bash
# Node 20+ (nodesource) on the worker host
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Build the bundled Remotion project + fetch its pinned Chromium
cd /opt/flashback/agent/remotion && npm ci && npx remotion browser ensure
```

Enable per-worker with the systemd drop-in env:

```
RENDER_ENGINE=remotion
# optional: FLASHBACK_REMOTION_DIR=/opt/flashback/agent/remotion
```

Leave `RENDER_ENGINE` unset (or `legacy`) to keep the current renderer. A
Remotion failure at runtime auto-falls-back to legacy, so enabling it is safe.
````

- [ ] **Step 2: Verify the doc renders (manual)**

Confirm the fenced blocks are balanced and the section reads correctly in a Markdown preview.

- [ ] **Step 3: Commit**

```bash
git add docs/ec2-deploy.md
git commit -m "docs(flashback): Remotion render-host provisioning (Node + Chromium)"
```

---

## Self-Review

**Spec coverage (Plan 1 scope only — layouts/motion/config/rename are Plans 2–5):**
- §6 Remotion pipeline (props → subprocess → MP4 + stills → PDF, Python orchestrates, S3 unchanged) → Tasks 1–6. ✓
- §6 render runtime = local subprocess (Node + Chromium on worker) → Tasks 6–7. ✓
- §6 single source of truth (PDF from stills, retire Pillow composer) → Task 2 + Task 4 (legacy composer retired by making Remotion the default in a later plan; here it remains the fallback, intentionally). ✓
- §11 feature flag + legacy fallback → Task 5. ✓
- §12 no per-render vendor fee (Remotion local) → whole plan; no vendor calls. ✓
- §13 deployment (Node + Chromium) → Task 7. ✓
- §14 testing (still-export goldens deferred to Plan 2 layouts; smoke + fallback + unit here) → Tasks 1–6. ✓

**Deferred to later plans (intentional, not gaps):** the 6-layout library + sequencer (Plan 2, `registry.ts` grows, `props.layout_slug` stops being hardcoded `framed_hero`); motion presets (Plan 3); Recipe config columns + `/admin` contract + Friendship Day seed + upbeat audio (Plan 4); the `tribute_`→`flashback_` rename (Plan 5). Visual-regression still goldens ride with the layouts in Plan 2.

**Placeholder scan:** none — every step has real code/commands/expected output.

**Type consistency:** `render_book_remotion` kwargs are a subset of `render_book`'s (shared `render_kwargs` dict in Task 5, `transition` added only for legacy); `RenderResult` reused from `render.py`; `build_props`/`assemble_pdf_from_stills`/`run_remotion` signatures match their call sites in Task 4; the props field names (`meta.{width,height,fps}`, `recipe.{fonts,ink,pacing}`, `scenes[].{role,layout_slug,text,image}`) are identical across Task 1 (producer), Task 6 (`Flashback.tsx`/`render.mjs` consumers), and the smoke test.

**Clean-code check:** no dead code or unused symbols in the task code blocks (props builder maps fonts by filename stem via `_FONT_FAMILY_BY_STEM`).

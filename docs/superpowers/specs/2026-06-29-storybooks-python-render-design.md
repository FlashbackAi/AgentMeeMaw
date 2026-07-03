# Storybooks — Python-owned render (collection templates)

**Date:** 2026-06-29
**Status:** design — awaiting review
**Sibling of:** `2026-06-20-tribute-video-pipeline-design.md` (same Python-render
pattern, generalized to many tag-driven templates).

---

## 1. Goal

Bring **storybook rendering into this Python repo** (off Node), driven by the
**collection templates** designed in Figma. A user picks a *collection*
(Childhood Memories, Festivals & Special Days, Adventurous/Crazy/Sad,
Interesting Stories, Nostalgia, …); the agent selects the moments that fit,
writes per-page narration in that collection's voice, and renders a **PDF +
per-page PNGs** with Gemini illustrations composited into the collection's
template. Output is reviewed against a real production legacy across **every
collection/template** before the Node contract + renderer are retired.

## 2. Locked decisions (from brainstorming)

1. **Text = hybrid per template.** Default: the compositor overlays the
   LLM-written narration into the template's text boxes (brand font, pixel-
   perfect, editable). Per-template opt-in: `baked` — the narration is rendered
   *inside* the Gemini image (for hand-lettered / in-scene styles).
2. **User picks the collection.** The agent then selects the fitting moments,
   assembles narration in the collection's style, and renders. (Not LLM-picked
   tags; not fully automatic.)
3. **Output = PDF + per-page PNGs** (the PNGs drive the in-app flip-through). No
   video (that's the tribute's job).
4. **Shared render core (Approach 1).** Extract the identical primitives from
   `tribute_video` into `flashback/page_render/`; both features build on it.
5. **Fixed page count per collection.** Each collection's template set has a
   known number of pages — so Node can mint exactly `cover + N pages + pdf`
   presigned PUT URLs up front.
6. **Validation-first.** Render every collection against a real prod legacy and
   eyeball every template BEFORE migrating the contract / retiring Node.
7. **Figma has examples, not coordinate-marked boxes.** We hand-author each
   template's image-zone + text-box rects from the skeleton + example (as the
   tribute template's fractional zones were tuned), not by importing Figma coords.

## 3. End-to-end flow

```
user picks collection → Node mints presigned URLs (pdf + cover + N pages PUT, prime-photo GET?)
  → POST /storybooks { collection, urls, ... }
     agent: select fitting moments → assemble narration (collection voice) → store render context
            → enqueue storybook_render
  ┌──────────────── storybook_render worker (Python, NEW) ────────────────┐
  │ load context → per page: Gemini illustration → manifest compositor      │
  │   (art into image zone; overlay narration into text boxes, or baked)    │
  │ → PDF + per-page PNGs → presigned PUT → status='complete' + NOTIFY       │
  └────────────────────────────────────────────────────────────────────────┘
  Node: LISTEN storybook_render_complete → write pdf_url + page_urls (+ cover)
        builder shows flip-through + download
```

## 4. Shared `page_render` core

Refactor (no behavior change): move the byte-identical pieces out of
`tribute_video` into `flashback/page_render/`:
- `art.py` — the Gemini `Artist` (character ref, scene illustrate, portrait-from-
  photo) + house style / negative prompt.
- `primitives.py` — Pillow helpers: cream/green blend, feather, chroma-key,
  autocrop, tone-match, font load + fit + wrap + draw, presigned `transfer`.

`tribute_video` imports from `page_render`; `tribute_video.compose`/`video`/
`render`/`assembler` stay. Storybook adds its own manifest-driven compositor on
top of the same core.

## 5. Collections + template manifest

A manifest module `flashback/storybook/collections/` (Python dataclasses; one
entry per collection):

```
Collection(
  slug, display_name,
  narration_style: str,          # voice/length prompt fragment (kids vs lyrical…)
  cover: Cover(template_png, title_box, subtitle_box?, image_zone?),
  layouts: [ Layout(                # FIXED ordered list → page count
      template_png,
      image_zone: Box,             # fractional rect on the page
      text_boxes: [TextBox(box, font, align, max_lines)],
      text_mode: "overlay" | "baked",
      art_hint: str,               # per-layout art-direction nudge
  ), ... ],
)
```

- Template PNGs ship as package data: `flashback/storybook/assets/<collection>/`.
- Zones are **hand-authored** from the Figma skeleton + example (the green-
  placeholder shows where art/text go; the example shows the target look).
- Page count = `len(layouts)`. Layouts already encode the variation
  (text-left/image-right ↔ flipped, hero, etc.) — no separate rotation logic.

**`GET /storybook-collections`** exposes the public list (slug, display_name,
page_count, cover/example thumbnail) so the builder renders the chooser and Node
knows how many page PUT URLs to mint.

## 6. Selection + assembly

Extend the assembler into a collection-aware mode (prefer a focused
`assemble_storybook_collection` over overloading the tribute assembler — the
shape differs):
- **Inputs:** the collection (`narration_style` + a category steer), the
  person/ground-truth, and the qualifying moment pool (optionally pre-filtered by
  the matching theme when the collection maps to one).
- **Output:** a `Book` = cover + exactly `page_count` pages; the LLM **selects +
  orders the fitting moments**, writes per-page narration in the collection's
  voice sized to each layout's text boxes, and emits per-page `art_direction`.
- Each page is bound to its layout (by index). For a `baked` layout, the
  narration is handed to the image prompt, not the overlay.
- **Floor:** a collection needs ≥ `page_count` qualifying moments; below that
  `POST /storybooks` returns 409 (mirrors today's `STORYBOOK_MIN_PAGES`).

## 7. Compositor + render

`flashback/storybook/compose.py` (manifest-driven, on `page_render`): per page →
load `template_png` → place the Gemini illustration into `image_zone` (blended
per the template's paper) → overlay each `text_box` with the page narration
(skip when `text_mode='baked'`) → page PNG. `render.py` assembles the PDF and
returns the ordered page PNGs. No video.

## 8. `storybook_render` worker + queue + config

`flashback/workers/storybook_render/` mirrors `tribute_render`: consume
`storybook_render` (trigger-only payload; Postgres authoritative) → load context
→ render → presigned PUT (pdf + cover + each page) → `status='complete'` +
transactional `NOTIFY storybook_render_complete` → Node writes the URLs. Ack on
ok/skip; redrive → `'failed'` on the final attempt. New
`STORYBOOK_RENDER_QUEUE_URL`; reuse `GEMINI_API_KEY`/`GEMINI_IMAGE_MODEL` +
render tunables; `StorybookRenderConfig`.

## 9. Data model + migration

Extend the existing `storybooks` table (don't fork it):
- `collection TEXT` — the chosen collection slug.
- `pdf_url TEXT` — Node-written (the book PDF).
- `page_urls JSONB` — Node-written ordered array of per-page PNG URLs.
- `status` gains `'failed'`; add `rendered_at TIMESTAMPTZ`, `render_error TEXT`.
- A read surface (columns or a small view) the builder reads: collection,
  status, pdf_url, page_urls, cover image_url, rendered_at.

`latest_generation_context['storybook']` carries the render context (collection,
Book, presigned URLs, blend/style, composed_at) — same pattern as tributes.

## 10. Route + Node contract + retirement

- `POST /storybooks` gains `collection` (required) + presigned URLs
  (`pdf_put_url`, `cover_put_url`, `page_put_urls[N]`, `prime_photo_get_url?`).
  It selects moments, assembles, stores context, enqueues `storybook_render`.
  Floor check → 409.
- `POST /storybooks/{id}/regenerate` + `/edit` re-render through the same worker.
- `GET /storybook-collections` — the chooser surface.
- **Retire** the Node storybook renderer + its `artifact_generation` consumption
  for storybooks; Node mints presigned URLs, `LISTEN`s
  `storybook_render_complete`, writes `pdf_url`/`page_urls`. Captured in
  `docs/STORYBOOK_PYTHON_NODE_PROMPT.md` (like the tribute handoff).

## 11. Validation-first sequencing (the gate)

Before any contract change or Node retirement:
1. Land the template **assets + manifests** for every collection (Figma PNG
   export + hand-authored zones).
2. Run a **prototype/spike** (`scripts/storybook_collections_prototype/`, like
   the tribute spike) that, for a **real production legacy** (read-only moments),
   renders **one storybook per collection** → PDF + page PNGs into `out/`.
3. **Eyeball every collection's every layout** — zones, narration voice/length,
   hybrid text placement, art style, cover. Iterate manifests + prompts until all
   collections look right.
4. Only then: build the worker/queue/migration/route + retire the Node
   contract + integration.

Acceptance = every collection renders a correct, on-template storybook from real
data.

## 12. Out of scope

- Video output (tribute owns that).
- Importing Figma coordinates (we hand-author zones from examples).
- Auto-collection-pick by the LLM (user picks).
- Node-side code (their repo) — documented only.

## 13. Risks

- **Figma access:** the MCP lacks edit access to the file; template PNGs must be
  exported into the repo (user action) before manifests can be authored. Hard
  prerequisite for the validation phase.
- **Render cost/time:** N Gemini calls per storybook × every collection during
  validation. One render per SQS message, generous visibility, DLQ.
- **Zone drift across templates:** each collection's zones are hand-tuned;
  validation across all collections is what catches mis-placed text/art.
- **Fixed page count vs thin legacies:** the floor (≥ page_count moments) gates
  generation; document the minimum per collection.
- **Likeness:** any portrait-from-photo page reuses the consented-cover relaxed
  negative (as today).

## 14. Phasing

0. `page_render` shared-core refactor (extract from `tribute_video`).
1. Collections manifest + asset pipeline (export PNGs, author zones).
2. Collection-steered assembler + manifest compositor.
3. **Validation spike** against a real prod legacy — every collection (the gate).
4. `storybook_render` worker + queue + config; migration; `/storybooks` rework +
   `GET /storybook-collections`.
5. Contract docs + retire the Node storybook renderer (only after step 3 passes).

Each phase: build → test (manifest compositor with placeholder art; assembler +
worker mocked) → DB-test where it touches Postgres.

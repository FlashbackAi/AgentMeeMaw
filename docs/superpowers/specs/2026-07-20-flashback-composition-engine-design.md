# Flashback: the composition-engine overhaul

**Date:** 2026-07-20
**Status:** Design — awaiting review
**Supersedes/extends:** `2026-06-20-tribute-video-pipeline-design.md` (render moves
from Pillow/ffmpeg to Remotion), `2026-07-14-tribute-campaign-crm-design.md`
(the Campaign/relationship/visual-theme config gains new Recipe levers).

---

## 1. Problem

Today every "tribute" video is the **same solemn slideshow**: a fixed page
template (portrait in a box at the bottom, an 8–10 word line in a box at the
top, a border), the same three crossfades, a 1.05× Ken Burns drift, and the
same sentimental piano — regardless of occasion or relationship. A Friendship
Day video and a memorial come out feeling identical, and both feel like a
funeral. The output has no art direction, no per-occasion personality, and no
reason to feel memorable.

Two root causes, in priority order:

1. **Composition is rigid.** Every frame is the same box layout. This — not the
   lack of motion — is what makes it feel lame. A moving boring box is still a
   boring box.
2. **Occasion is a skin, not a director.** The Campaign config only swaps fonts,
   inks, template, and an audio file. It cannot change the *shape* of the film:
   the narrative arc, the layouts, the pacing, the voice.

## 2. Goals

- One person's stories, run through two Campaigns, produce **genuinely different
  films** — one makes you laugh, one makes you cry.
- **Art-directed composition variety**: a library of distinct scene layouts,
  sequenced so no two frames look alike.
- **Everything creative is control-panel data**, not hardcoded. Adding an
  occasion is config; adding a *layout* is a small code+register task that then
  appears as a control-panel option everywhere.
- **No external video vendor.** Self-owned: Gemini (art, existing) + Remotion
  (composition + motion, local compute).
- Rename the output from "tribute" to **Flashback** — occasion-neutral, on-brand.
- Ship without breaking the current pipeline (feature-flagged, with automatic
  fallback to today's render).

## 3. Non-goals / explicitly dropped

- **No image-to-video / "living paintings."** Higgsfield and every external
  gen-video model are **dropped** — not deferred, dropped. Motion comes entirely
  from Remotion. (If revisited, it re-enters as a scene's video source inside a
  Remotion layout, but it is out of scope here.)
- No photoreal subject, no voice cloning, no deepfake — unchanged product rules.
- No admin-authored layouts from scratch. Admins *choose from* and *sequence* a
  professionally-built layout library; they do not draw new layouts.
- The full schema/Node-contract rename is sequenced as its own track and does
  **not** block the creative overhaul (see §10).

## 4. Core concepts

**Flashback** — the output artifact (formerly "tribute video"). A short,
art-directed, painterly film about one person. "Send them a Flashback."

**Campaign as creative director** — the Campaign (occasion) × relationship is
promoted from "a skin" to the entity that decides the *whole shape* of the
Flashback. The old reverent, slow, framed, piano default becomes **just one
Recipe** (the memorial one), not the baseline everything inherits.

**Recipe** — the full creative specification a Campaign resolves to at
generate-time. Six levers, **all authored in the control panel**, all
snapshotted into the render context at `/generate` (a CRM edit only affects a
Flashback on manual regenerate — unchanged invariant). Missing/broken config
always degrades to the safe memorial Recipe; a render never blocks on config.

## 5. The Recipe model

| Lever | Config entity (control panel) | Status |
|---|---|---|
| **Voice / tone** | `relationship_profiles` | exists |
| **Look / fonts / inks** | `tribute_visual_themes` | exists |
| **Music** | `tribute_visual_themes` (audio) | exists; needs an upbeat track added to `AUDIO_REGISTRY` |
| **Narrative arc** | `relationship_profiles` (+ campaign override) | **new** — ordered beat-intent slots; LLM-assisted authoring via existing `/admin/tribute_config/generate` |
| **Layout palette + role pins** | `tribute_visual_themes` | **new** — allowed layout slugs + opener/payoff/closing pins |
| **Motion preset** | `tribute_visual_themes` | **new** — Remotion motion style: `punchy` / `playful` / `calm` / `cinematic` |
| **Pacing** | `tribute_visual_themes` | **new** — hold + transition durations |

The single most important change: **the narrative arc becomes data authored per
campaign**, instead of the one hardcoded `cover → beats → message → closing`
skeleton in `book.py`. That is what turns "same skeleton, different paint" into
"different films."

## 6. Architecture: the Remotion pipeline

Remotion is treated as **"just another render binary,"** the way ffmpeg is
today — invoked as a local subprocess, no DB/S3/secrets of its own.

```
Gemini Artist          Python worker                     Remotion (Node, local)
(painterly stills)  →  resolve Recipe snapshot      →    scenes as components
                       assemble PROPS JSON:               (layouts + motion)
                        - ordered scenes                        │
                        - per-scene: layout_slug,               ▼
                          art image, text, timing,         headless Chromium
                          role                                render
                        - global: fonts, inks,                  │
                          music, motion_preset,       ┌─────────┴──────────┐
                          pacing                      ▼                    ▼
                                                     MP4          per-scene stills
                                                      │                    │
                       Python PUTs MP4 + poster ◀─────┘          Python assembles
                       via Node-minted presigned URL              PDF from stills
```

- **Python stays the orchestrator.** Generates the art (Gemini, unchanged),
  resolves the control-panel Recipe into a snapshot, produces one **props JSON**
  describing the entire film as data, invokes Remotion as a subprocess, gets back
  an MP4 + a directory of per-scene still PNGs, assembles the PDF from the stills,
  and PUTs the MP4 + poster to S3 **via the Node-minted presigned URLs exactly as
  today**. The S3 boundary rule (no S3 creds, never the URL columns) is untouched.
- **Remotion owns only composition + render.** The layout library lives here as
  components; the props JSON drives which layout each scene uses and how it
  animates. Pure `props → pixels`.
- **Single source of truth.** Remotion renders both the MP4 and the still frames;
  the PDF is assembled from those stills. The old Pillow page-composer
  (`compose.py` box layout) is **retired**.

### Render runtime

**Local subprocess on the render worker.** A Remotion Node project is bundled
in-repo; the Python worker shells out to a thin Node CLI (`props.json → out.mp4 +
stills/`). Chosen over Remotion Lambda (which would need S3 creds → boundary
friction) and a persistent sidecar (a new always-on service). Cost: the render
host gains **Node + headless Chromium** (see §13).

### Props JSON contract (Python ↔ Remotion)

The stable interface between the two halves. Sketch:

```jsonc
{
  "meta": { "flashback_id": "...", "aspect": "9:16", "fps": 30 },
  "recipe": {
    "fonts": { "main_slug": "caveat", "eyebrow_slug": "nunito" },
    "ink":   { "main_fill": "#2a4d69", "accent": "#e8552e" },
    "audio_slug": "upbeat_indie",
    "motion_preset": "punchy",
    "pacing": { "hold": 1.4, "transition": 0.6 }
  },
  "scenes": [
    { "role": "opener",  "layout_slug": "split_duotone",
      "text": "HOW WE MET", "art_url": "file:///tmp/art/opener.png", "duration": 2.2 },
    { "role": "beat",    "layout_slug": "scrapbook",
      "text": "the summer we never grew up", "art_url": "...", "duration": 2.0 },
    { "role": "payoff",  "layout_slug": "type_over_crop",
      "text": "STILL RIDE-OR-DIE", "art_url": "...", "duration": 2.4 },
    { "role": "closing", "layout_slug": "fullbleed_caption",
      "text": "…to the next chapter", "art_url": "...", "duration": 3.0 }
  ]
}
```

## 7. Layout library

Six primitives ship in v1, each a Remotion component registered under a stable
slug, each built to work with **real, variable content** (variable-length lines,
portrait vs. scene art, safe margins):

| slug | composition |
|---|---|
| `portrait_left` | portrait bleeds off the left edge; line stacks down the right |
| `fullbleed_caption` | art fills the frame; small italic caption in a corner |
| `type_over_crop` | giant kinetic headline over a tight detail crop |
| `scrapbook` | overlapping polaroids + handwritten scrawl |
| `split_duotone` | vertical split: art one side, bold color block + title the other |
| `framed_hero` | today's calm framed center hero (the memorial default) |

Adding a seventh = build the component + register the slug; it then auto-appears
as a control-panel option for every campaign. This library is our craft; it is
not admin-authored.

## 8. Layout sequencing

Because beat count varies per person, layouts are sequenced **at render time**
from the Recipe:

1. **Honor role pins first** — opener / payoff / closing use their pinned slug.
2. **Fill remaining beats from the palette** with a **no-two-in-a-row** rule and
   light **content-matching** (a portrait-heavy beat → `portrait_left`; a
   one-line punch → `type_over_crop`).
3. **Degrade safely** — empty/unknown palette → `framed_hero`. Config never
   blocks a render.

Admin control = **palette (allowed slugs) + role pins**; the code orders the
rest. Survives any beat count with low admin effort while still guaranteeing the
"how we met opens the film" moment.

## 9. Motion

Motion is **Remotion-only** — parallax layers, kinetic typography, element
entrances, drift/zoom. No external model. The control-panel **motion preset**
(`punchy` / `playful` / `calm` / `cinematic`) selects a code-defined animation
style applied across the film's scenes. Friend → `punchy`; memorial → `calm`.

## 10. The rename (Flashback) — sequenced, non-blocking

The full schema + Node-contract rename is real cross-repo work and is
**orthogonal to the creative magic**. Sequenced so nothing blocks shipping:

1. **User-facing + new code** — everything users/admins see says "Flashback";
   new render code uses `flashback_*` names.
2. **Compatibility layer** — DB compatibility views over the existing
   `tribute_*` tables; **dual-emit** the completion NOTIFY
   (`flashback_render_complete` alongside `tribute_render_complete`) so Node keeps
   working on the old contract.
3. **Node cutover** — the Node team migrates endpoints + NOTIFY channel on their
   timeline.
4. **Drop aliases** — remove the old views/channels once Node is fully cut over.

Worst case: magical Flashbacks ship while a few internal tables still read
`tribute_` for a few weeks. Acceptable.

## 11. Coexistence & fallback

Feature flag `FLASHBACK_RENDER_ENGINE` (`remotion` | `legacy`). A Remotion render
failure **auto-falls-back** to today's Pillow/ffmpeg render. No hard cutover;
launches never break. The legacy path is retained (and tested) until Remotion is
proven in production.

## 12. Cost

- **Remotion local render** — compute only, **no per-render vendor fee**.
- **Gemini art** — unchanged.
- **No external gen-video** — $0 (Higgsfield dropped).

## 13. Deployment (real ops change)

The render worker host (EC2, ap-south-1) gains **Node + headless Chromium** for
the bundled Remotion project. New install/provisioning step; flagged because it
touches the production environment. Bundle Chromium via Remotion's own
`ensureBrowser`/pinned revision to keep it reproducible.

## 14. Testing

- **Visual regression** — Remotion single-frame still export → golden PNGs per
  layout.
- **Render smoke test** — a golden props JSON → full render, asserting page count
  + MP4/PDF/poster outputs.
- **Fallback path** — existing render tests continue to guard the legacy engine.
- **Sequencer unit tests** — palette + role pins + beat count → deterministic
  layout order (no-repeat, pins honored, safe degrade).

## 15. Friendship Day Recipe (launch instance — all control-panel data)

| Lever | Value |
|---|---|
| Arc | how we met → the chaos era → why you're my person → still ride-or-die |
| Voice | warm, funny, roast-y, inside-jokey; punchy captions |
| Layout palette | `scrapbook`, `type_over_crop`, `split_duotone`, `fullbleed_caption` |
| Role pins | opener→`split_duotone` · payoff→`type_over_crop` · closing→`fullbleed_caption` |
| Music | upbeat *(new track needed in `AUDIO_REGISTRY`)* |
| Pacing | fast — short holds, snappy transitions |
| Motion preset | `punchy` |

## 16. Open questions / loose ends

- **Upbeat audio asset** — v1 needs at least one non-piano track licensed and
  added to `AUDIO_REGISTRY` for Friendship Day.
- **Node CRM screens** — new control-panel UI for: layout palette + role-pin
  picker, motion-preset picker, pacing, and the arc beat-slot editor. Agent
  specs the `/admin/tribute_config/*` contract; Node builds the UI.
- **Arc authoring UX** — ordered beat-intent slots with an LLM-generated starter
  the admin tweaks (reuses `/admin/tribute_config/generate`).
- **Chromium provisioning** — pin a Chromium revision; decide bundled vs.
  system install on the worker AMI.
- **PDF fidelity** — confirm Remotion still exports at print resolution (150 DPI
  equivalent) for the A4-portrait PDF.

## 17. Build order

1. Remotion project scaffold + Node CLI (`props.json → mp4 + stills`) + Chromium
   provisioning on the worker.
2. Props JSON contract + Python assembler that resolves a Recipe snapshot into
   props (replacing `book.py`'s fixed skeleton).
3. The six layout components + the sequencer (palette + role pins + rules).
4. Motion presets.
5. Wire the render worker: Gemini art → props → Remotion subprocess → PDF from
   stills → S3 PUT via presigned URLs; behind `FLASHBACK_RENDER_ENGINE` with
   legacy fallback.
6. Recipe config extensions (new columns/fields) + `/admin/tribute_config/*`
   contract for the new levers.
7. Friendship Day Recipe seed + upbeat audio asset.
8. Visual-regression + smoke + sequencer tests.
9. The Flashback rename track (§10) — independent, non-blocking.

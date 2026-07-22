# CRM Prompt — Video style: Flashback vs Classic (the two render flows)

**For:** the control-panel (CRM) team + product.
**Status:** implemented in `flashback_agent_admin` (theme editor → recipe
section); the agent field + values are live (migration 0045,
`tribute_visual_themes.render_engine`). This doc is the record of the
distinction and the UI contract.

---

## TL;DR

A tribute video renders in one of **two styles**, chosen per **visual theme**:

- **Flashback** — the new Remotion engine: a short, art-directed *animated*
  painterly film (distinct scene layouts, kinetic type, motion, transitions).
  **This is the default for every relationship and occasion.**
- **Classic** — the original Pillow/ffmpeg *slideshow*: calm framed stills with
  gentle transitions. The pre-Flashback look.

We deliberately **keep both**. Classic is (a) a real stylistic choice for themes
that want the quiet, memorial slideshow feel, and (b) the automatic safety net —
if the Flashback engine ever fails or isn't installed on a render host, the
render falls back to Classic instead of failing. So the choice is never a
one-way door and a render never strands.

Both outputs are still called a **"Flashback"** in end-user copy (the product
name); "Classic vs Flashback" is an internal/admin styling distinction, not two
separate products.

---

## 1. The control — "Video style" (theme editor)

In the visual-theme editor's recipe section, the **first** control is a two-way
**Video style** picker:

```
Video style:
  ( • ) Flashback   animated — layouts, kinetic type, motion
  (   ) Classic     calm slideshow (the original look)

  Every relationship gets a Flashback by default. Classic also renders
  automatically if the animated engine ever fails.
```

Behavior:

- **Default is Flashback.** A theme with nothing set renders as a Flashback.
- **Picking Classic hides the Flashback-only controls.** Layout palette, role
  pins, pacing, motion preset, and accent all disappear (they don't apply to a
  slideshow), replaced by a one-line note. This keeps the form honest — no
  animation knobs on a theme that won't animate.
- **Picking Flashback reveals** the full "Flashback recipe" block again.

## 2. Storage / agent contract

The choice is one field inside the theme's `recipe`:

```jsonc
"recipe": { ..., "render_engine": "" }   // '' | 'remotion' | 'legacy'
```

- `''` (unset, worker default) and `'remotion'` both mean **Flashback**.
- `'legacy'` means **Classic**.
- The picker writes `'remotion'` when Flashback is chosen (explicit, so the
  snapshot is self-documenting) and `'legacy'` when Classic is chosen. It treats
  unset as Flashback for display, so there's no confusing third option.

No new endpoint. It rides the existing
`/admin/tribute_config/tribute_visual_themes` CRUD (nested `recipe` block,
whole-recipe-replace semantics — see the rollout prompt).

## 3. When a change takes effect

Renders read the **config snapshot** taken at `POST /tributes/{id}/generate`, so
switching a theme's Video style only affects a video on its **next generate /
manual regenerate**. Already-rendered videos never change. (Same rule as every
other recipe lever.)

## 4. Guidance for admins (surface as help text / onboarding)

- **Default to Flashback** for essentially everything — it's the product's
  signature look.
- **Pick Classic** only for a theme that should intentionally feel like a quiet,
  framed slideshow (e.g. a solemn memorial register where motion would feel
  wrong).
- You don't need Classic "just in case the engine breaks" — that fallback is
  automatic and independent of this choice.

## 5. What is NOT changing

- The end-user artifact is still a "Flashback" (MP4 + PDF + poster), same
  `/generate` request, presigned URLs, `tribute_render_complete` NOTIFY, URL
  columns.
- Classic is not being retired. It stays as a style + the crash-fallback.
- No relationship/occasion is excluded — every one produces a video; Video style
  only decides *how* it looks.

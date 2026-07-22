# CRM/Frontend Prompt — Fix theme creation: Flashback-first, with a Classic toggle

**For:** the control-panel (CRM) frontend team.
**Screen:** Visual Themes (`/crm/themes`, `ThemesScreen.tsx` + `ThemeEditModal`).
**Type:** UX flow fix. No agent/API changes required — the backend already
supports everything below.

---

## The problem

Theme creation is **Classic-first and wrong for the current product.** The only
way to make a theme today is the "GENERATE TEMPLATE CANDIDATES" flow: type a
**brief** → the agent paints up to 4 **background template images** → you pick
one. That produces a **Classic** theme (a painted paper background the old
slideshow renderer composites stills onto).

But **Flashback is now the default renderer**, and Flashback layouts
(`split_duotone`, `scrapbook`, `type_over_crop`, …) **paint their own
backgrounds in code** — they never use a generated template image. (Agent fact:
`remotion_render.py` — *"Remotion composites art into its OWN layout
backgrounds"*; the generated template only gates a minor art-mood flag.)

So today, to get a Flashback theme you must **create a Classic one and then edit
it** — brief, generate 4 images you'll never use, pick one, save, reopen, switch
Video style to Flashback, configure the recipe. That's backwards.

## The fix — one toggle at the top of create, Flashback default

Put a **Video style toggle at the very top of the theme create surface** that
switches the whole form between two modes. Default = **Flashback**.

```
New theme
  Video style:  ( Flashback ●———○ Classic )     ← default Flashback

  ── when Flashback (default) ─────────────────────────────
     Display name, Slug
     Fonts (main + eyebrow), Ink (main / eyebrow / accent), Audio
     Recipe: Layout palette (chips) · Role pins · Pacing · Motion preset
     [ Create ]                       ← NO brief, NO image generation

  ── when Classic ─────────────────────────────────────────
     Display name, Slug, Fonts, Ink, Audio
     Brief  → [ Generate 4 background candidates ] → pick one
     [ Create from pick ]             ← the CURRENT flow, unchanged
```

The toggle is the **same concept** as the Video-style control already in the
theme **edit** modal (it reads/writes `recipe.render_engine`). Create and edit
are now consistent: the toggle sets the engine, and the engine decides which
controls exist.

## Why this works without backend changes

- **Flashback create** is a plain config create — `POST /admin/tribute_config/
  tribute_visual_themes` with `{ slug, display_name, fonts:{main_slug,
  eyebrow_slug}, ink:{main_fill, eyebrow_fill, accent?}, audio_slug,
  recipe:{ layout_palette, layout_pins, pacing, motion_preset,
  render_engine:'remotion' } }`. **No `template_image`** — the agent already
  forbids image bytes in CRUD payloads and a Flashback render doesn't need one.
- **Classic create** is exactly today's flow: `POST /visual_themes/generate`
  (brief → candidates) → pick → the picked candidate is saved with its template
  image. Set `recipe.render_engine:'legacy'` on it (or leave empty and let the
  editor default — but prefer setting it explicitly so the row is
  self-documenting).
- `render_engine` lives inside the nested `recipe` block (whole-recipe-replace
  semantics, per the recipe rollout prompt). `''` and `'remotion'` both mean
  Flashback; `'legacy'` means Classic.

## After create

- **Flashback**: open straight into the recipe editor (layout palette / pins /
  pacing / motion / accent) so the user configures the look immediately. There
  is nothing to "pick" — the theme is already valid and renders on the default
  Friendship recipe if left untouched.
- **Classic**: unchanged — the picked background becomes the theme; editing
  shows the brief/regenerate affordances.

## Related cleanups (do these in the same pass)

1. **Themes screen headline + Explainer** currently say "Generate up to 4
   background candidates from a brief… the paper the pages are printed on."
   That describes only Classic. Reframe: a theme is the **look** (fonts, ink,
   music, and — Flashback — the layout recipe; Classic — a painted background).
2. **"Render sample page"** renders a composited *template* page — a Classic
   view. For a Flashback theme it should render a **layout frame** (or be
   labelled "Classic preview") rather than implying a paper page.
3. **Brief / generate-background UI** must not appear anywhere for a Flashback
   theme — not in create, not in edit. It exists only in Classic mode.

---

## REQUIRED — your frontend prompt/implementation MUST resolve these

1. **Default is Flashback.** Opening "New theme" shows the Flashback form; the
   user opts into Classic via the toggle. Never the reverse.
2. **No brief in Flashback mode.** The brief + "generate candidates" controls
   are hidden entirely unless the toggle is on Classic. A Flashback theme is
   created with zero image-generation calls.
3. **Create is one step, not create-then-edit.** A Flashback theme is valid on
   create with just name/slug/fonts/ink/audio (recipe optional — empty recipe =
   the proven default). Do not require a round-trip through the editor to make
   it a Flashback theme.
4. **The toggle maps to `recipe.render_engine`** — Flashback → `'remotion'`,
   Classic → `'legacy'` — and matches the edit modal's existing Video-style
   control, so a theme's kind is consistent between create and edit.
5. **Classic path is preserved, not removed.** Everything that exists today for
   brief → generate → pick still works; it just lives behind the Classic side
   of the toggle.

## Backend contract recap (already shipped — do not rebuild)

- Create/edit visual themes: `/admin/tribute_config/tribute_visual_themes`
  (nested `recipe` block; `render_engine` inside it).
- Generate a Classic background: `/visual_themes/generate` (brief → candidates).
- Layout/motion catalog for the recipe pickers: `GET /flashback/layouts`.
- Everything above is live; this is a pure frontend flow change.

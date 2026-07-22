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

---

## Field reference (exact values — don't invent)

**Fonts** (`fonts.main_slug`, `fonts.eyebrow_slug`) — pick from
`GET /crm/admin/asset-library` → `fonts`. Current registry:
`caveat`, `eb_garamond`, `nunito`, `playfair_italic`. Both slugs required.

**Audio** (`audio_slug`) — from asset-library `audio`. Current registry has one
track: `sentimental_piano`. Required (default it).

**Ink** (`ink`) — object, hex `#rrggbb`:
- `main_fill` (required) — the serif body/line ink.
- `eyebrow_fill` (required) — the small-caps eyebrow ink.
- `accent` (optional) — bold blocks / underlines in Flashback layouts.

**Recipe** (`recipe`, Flashback only) — all optional; empty = the proven
Friendship default (a bare Flashback theme still renders). Catalog comes from
`GET /crm/flashback/layouts` → `{ layouts, motion_presets, pinnable_roles }`:
- `layout_palette: string[]` — allowed layout slugs. The 13 today:
  `split_duotone, scrapbook, type_over_crop, fullbleed_caption, framed_hero,
  letter_note, filmstrip, postcard, word_mask, torn_reveal, gallery_wall,
  magazine, map_journey`.
- `layout_pins: { opener?, payoff?, closing? }` — a slug (from the palette)
  pinned to a structural role. `pinnable_roles = [opener, payoff, closing]`.
- `pacing: { hold, transition }` — seconds (hold ~1.5–4, transition ~0.3–1.2).
- `motion_preset` — one of `calm | playful | punchy | cinematic` (or empty).
- `render_engine` — `'remotion'` (Flashback) | `'legacy'` (Classic) | `''`.

## Reuse these existing pieces — do NOT rebuild them

- **`RecipeSection.tsx`** already renders the whole Flashback recipe: the
  **Video style toggle** (Flashback/Classic → `recipe.render_engine`), the
  layout-palette chips (with preview art), role-pin selects, pacing + presets,
  motion preset, and the accent-ink picker. It also already hides the
  Flashback-only controls when Classic is selected. The create form should
  render this same component — the toggle work is done; you're lifting it up to
  govern the whole create surface, not just the recipe block in edit.
- **`ThemeEditModal.tsx`** — the edit surface; its Video-style toggle is the
  same control. Keep create/edit consistent by sharing `RecipeSection`.
- **`GeneratePanel.tsx` / `useGenerateThemes` / candidate grid** — the Classic
  brief→candidates flow. Move it under the Classic side of the toggle unchanged.
- **`useAssetLibrary`** — fonts + audio options for the pickers.
- **`useCreateConfig('visual_themes')`** — the plain create mutation for the
  Flashback path (no generate call).
- **`VisualThemeSelect`** — unchanged; profiles/campaigns still attach a theme.

## Concrete create payloads

**Flashback theme** (no image generation) — `POST … /tribute_visual_themes`:
```jsonc
{
  "slug": "warm_keepsake",
  "display_name": "Warm Keepsake",
  "fonts": { "main_slug": "playfair_italic", "eyebrow_slug": "eb_garamond" },
  "ink":   { "main_fill": "#3a2c1c", "eyebrow_fill": "#967648", "accent": "#e8552e" },
  "audio_slug": "sentimental_piano",
  "recipe": {
    "layout_palette": ["split_duotone", "scrapbook", "type_over_crop", "fullbleed_caption"],
    "layout_pins": { "opener": "split_duotone" },
    "pacing": { "hold": 2.4, "transition": 0.7 },
    "motion_preset": "punchy",
    "render_engine": "remotion"
  }
}
```
Minimal valid Flashback theme = `slug`, `display_name`, `fonts`, `ink`,
`audio_slug`, `recipe: { render_engine: "remotion" }` — everything else in
`recipe` can be omitted (defaults apply).

**Classic theme** — unchanged: `POST /visual_themes/generate` with `{ brief,
slug, display_name, n_candidates, fonts?, ink?, audio_slug? }`, pick a
candidate; ensure the saved row's `recipe.render_engine` is `"legacy"`.

## Sensible defaults for the Flashback create form (prefill these)

- fonts: `main_slug: "playfair_italic"`, `eyebrow_slug: "eb_garamond"`
- ink: `main_fill: "#3a2c1c"`, `eyebrow_fill: "#967648"`, `accent: "#e8552e"`
- audio: `sentimental_piano`
- recipe: empty palette/pins/pacing/motion (→ engine default), `render_engine:
  "remotion"`

So a user can type only a display name + slug and hit Create.

## Lifecycle & validation

- New rows are **drafts**; `Publish` makes them live. Editing a published row
  **supersedes** (new version); existing Flashbacks never change. Same as today.
- **Slug** must be unique among active rows (kebab/snake). A dup → 422
  `slug: already in use`.
- Validation errors return `{ detail: { errors: ["field: message", …] } }` —
  render each next to its field (the existing `FieldErrors` pattern).
- `template_image` is **never** in a CRUD payload (agent rejects it); it only
  arrives via `/visual_themes/generate`. So a Flashback create simply has no
  image and `has_image: false` — that's correct, not an error.

## Acceptance checklist

- [ ] "New theme" opens in **Flashback** mode by default; no brief visible.
- [ ] A Flashback theme can be created with only display name + slug (defaults
      fill the rest) and **zero** image-generation calls.
- [ ] Flipping the toggle to **Classic** reveals the brief → generate → pick
      flow and hides the recipe controls; flipping back hides the brief.
- [ ] The created row's `recipe.render_engine` matches the toggle
      (`remotion`/`legacy`) and the edit modal shows the same state.
- [ ] After a Flashback create, the user lands in the recipe editor (palette /
      pins / pacing / motion / accent), not a "pick a background" step.
- [ ] Classic create still works end to end (brief → 4 candidates → pick →
      publish), unchanged.
- [ ] Themes headline/Explainer no longer describe only "generated background".

## Copy

- Toggle: **Flashback** (animated — layouts, kinetic type, motion) ·
  **Classic** (a painted background, slideshow style).
- Flashback create subhead: "Name it and pick a look — no image to generate."
- Classic create subhead (existing): "Describe it — the agent paints, you pick."

---

## The cover page (where it's set, and a proposed dedicated control)

**Today there is no cover-specific selector.** The cover/poster is the video's
first frame = the **opener** scene, so its look and motion come from existing
recipe controls:

- **Cover layout/style** = the **Opener role pin** (`layout_pins.opener`). Pin a
  layout to the opener and that's the cover. `(auto)` = the first palette layout.
- **Cover animation** = the **Motion preset** — but it's **global** to the whole
  Flashback, not cover-only. No per-cover motion exists.
- Adjacent, and NOT on the theme: **de-age cover art** is a profile/campaign
  toggle; the cover **title** is auto-generated.

So in the current CRM: theme → Flashback recipe → **Opener pin** (cover layout)
+ **Motion preset** (its motion). Surface this clearly — label the Opener pin
"**Cover / opener layout**" so it's discoverable as the cover control.

### Pick the cover VISUALLY — reuse the palette chip grid, not a dropdown

The role pins (Opener/Payoff/Closing) are plain `<select>`s today. That's the
wrong affordance now that the **Layout palette** renders a grid of chips with
**preview thumbnails** (Split/Duotone, Scrapbook, Big Type, … — the 13). The
cover picker should reuse **the exact same chip grid**, just in **single-select**
mode:

- Same chip component as the palette (preview image + label), rendered as a
  grid.
- **Single-select** (a radio-style pick) — one layout is the cover; the
  selected chip gets the `ring-2 ring-accent` treatment the palette chips
  already use.
- Scope it to the **selected palette** (only layouts this theme uses), so the
  cover is always a layout that actually appears; if the palette is empty, show
  the full catalog.
- This replaces the "Opener pin" dropdown with a visual "**Cover**" chip picker.
  Do the same for Payoff/Closing pins while you're in there — three small
  single-select chip rows read far better than three dropdowns.

Component-wise this is the palette chip loop from `RecipeSection` extracted into
a small `LayoutChoice` (props: `layouts`, `value | values`, `multi`,
`onChange`) — the palette passes `multi`, the cover/pins pass single-select.
Reuses `FlashbackLayout.preview_url` (already wired) so no new assets.

### First-class cover control (recipe fields to back the picker)

Back the visual cover picker with recipe fields so the cover is independent of
the body:
- `cover_layout` (a layout slug; defaults to `layout_pins.opener`, then the
  palette head) — set by the single-select cover chip grid above.
- `cover_motion` (a motion preset; defaults to the global `motion_preset`) — a
  small "match the film / …" select beside it.

Agent: `remotion_render` already assigns the opener scene first, so honoring a
`cover_*` override is a localized change (apply it to scene 0 instead of the
shared pins/motion). If you'd rather not touch the agent this pass, the picker
can write `layout_pins.opener` instead of a new `cover_layout` field — the
visual chip grid is the win either way; the dedicated `cover_*` fields are the
follow-up that makes the cover truly independent.

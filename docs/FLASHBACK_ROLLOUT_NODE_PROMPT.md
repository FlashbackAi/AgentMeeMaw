# Node Prompt — Flashback rollout: layouts proxy, recipe passthrough, FD pin

**For:** the Node Backend team (+ one control-panel config action).
**Scope:** ONLY the delta since `FLASHBACK_NODE_PROMPT.md` was applied.
Everything you already built (recipe UI, copy rename, render-swap
awareness) stays as-is — do not redo it.
**Agent status:** all merged to `main` — Remotion is now the **default**
render engine, 13-layout catalog live, motion presets live, per-theme
`render_engine` pin (migration 0045), nested-recipe API boundary fixed.

---

## 1. NEW proxy route — `GET /flashback/layouts` (the dashboard calls it)

The dashboard's layout picker fetches
`GET <node>/api/v2/legacy/crm/flashback/layouts` and currently falls back
to a provisional hardcoded table on your 404. The agent endpoint is live;
add the passthrough (service token, read-only, no admin gate needed
beyond your usual CRM auth):

```
GET /crm/flashback/layouts  →  agent GET /flashback/layouts
```

Response is an **envelope, not a bare array** (the original prompt
guessed wrong — the dashboard already handles both):

```jsonc
{
  "layouts": [{ "slug": "...", "label": "...", "description": "..." }],
  "motion_presets": ["calm", "playful", "punchy", "cinematic"],
  "pinnable_roles": ["opener", "payoff", "closing"]
}
```

Notes:
- `preview_url` is **not** served by the agent; the dashboard ships its
  own preview art. Don't synthesize it.
- 13 layouts now (8 new: `letter_note`, `filmstrip`, `postcard`,
  `word_mask`, `torn_reveal`, `gallery_wall`, `magazine`, `map_journey`).
  Slugs come from this endpoint — nothing to hardcode.

## 2. Recipe passthrough on the visual-theme proxy

If your `/admin/tribute_config/visual_themes` proxy whitelists payload
keys, allow the nested `recipe` block through in **both directions**:

- **Write:** payloads may carry
  `recipe: { layout_palette, layout_pins, pacing, motion_preset,
  render_engine }`. Forward verbatim. Semantics are **whole-recipe
  replace** — keys the block omits are cleared to engine default, so
  never merge/patch recipe server-side.
- **Read:** the agent now returns each row with the nested `recipe`
  block (flat `layout_palette`/`layout_pins`/`pacing`/`motion_preset`
  columns no longer appear at the top level of a row). If you map or
  validate row fields, update for the nested shape.
- New field since the original contract: `recipe.render_engine` —
  `''` (worker default) | `'remotion'` | `'legacy'`. Validation errors
  come back in the usual 422 `{detail: {errors: ["field: message"]}}`.

## 3. One config action — pin Father's Day to the classic look

Remotion (the Flashback engine) is now the worker's **default**: every
video generated from now on renders as a Flashback unless its visual
theme pins otherwise. Father's Day must keep its classic framed-slideshow
look:

1. Control panel → visual themes → the Father's Day theme.
2. Render engine → **Classic (legacy slideshow)** → save → publish.
3. Do this **before any FD tribute is regenerated**. (Already-generated
   videos are rendered files — they never change regardless.)

Everything else needs no action: unconfigured themes render Flashbacks
with the proven Friendship default recipe, and the new layouts appear in
videos only once an admin adds them to a theme's palette.

## 4. Ops awareness (no Node work — just know it)

- Agent deploy runs migrations 0044 + 0045 automatically.
- The tribute-render worker host needs the Remotion install (Node
  runtime + the agent repo's `remotion/` project; `FLASHBACK_REMOTION_DIR`
  overrides the path). Missing/broken install ⇒ the render silently
  falls back to the legacy look and logs
  `tribute_render.remotion_failed_fallback_legacy` — if users report
  "bordered" videos post-rollout, check that log line first.
- The render boundary is otherwise byte-identical: same `/generate`
  request, presigned URLs, `tribute_render_complete` NOTIFY, URL columns.

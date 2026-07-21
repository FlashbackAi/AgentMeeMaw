# Node Prompt — Flashback (Remotion) render + control-panel recipe

**For:** the Node Backend + control-panel (CRM) team.
**Status:** agent side — render engine + Python foundation **merged to `main`
(`68fbc70`, behind a flag)**; per-occasion recipe **config layer NOT built yet**
(migration 0044 + admin API forthcoming). Spec:
`docs/superpowers/specs/2026-07-20-flashback-composition-engine-design.md`.

---

## TL;DR

The tribute output is being reborn as a **Flashback** — a short, art-directed,
*animated* painterly film (multiple distinct scene layouts, kinetic type, motion,
FX), rendered by a new **Remotion** engine inside the agent's `tribute_render`
worker instead of the old Pillow/ffmpeg slideshow.

**Two things matter for you, and they're very different sizes:**

1. **The render swap itself needs ZERO Node work.** It's a drop-in behind an
   agent-side flag: same presigned URLs, same `tribute_render_complete` NOTIFY,
   same URL columns. Node cannot tell the difference. Nothing to do.
2. **Making occasions look *different* (a friend ≠ a memorial) needs
   control-panel work** — new "recipe" fields on the visual-theme editor —
   **paired with** the agent's forthcoming config layer. Contract below; **do
   not ship until the agent API accepts these fields** (we'll coordinate).

There is also a **naming** change (the artifact is now a "Flashback") that is
**user-facing copy only** for now — the schema/endpoints still say `tribute`.

---

## 1. What is UNCHANGED — do NOT touch

The Remotion render is internal to the agent's worker and is gated by an
agent-only env flag (`RENDER_ENGINE`). Everything at the Node boundary is
byte-for-byte the same as `TRIBUTE_VIDEO_NODE_PROMPT.md`:

- **`POST /tributes/{id}/generate`** — same request (`artifact_kind:
  'tribute_video'`, `video_put_url`, `pdf_put_url`, `poster_put_url?`,
  `prime_photo_get_url?`). No new fields required.
- **Presigned URLs** — you still mint GET (prime photo) + PUT (mp4/pdf/poster);
  the worker still holds no S3 creds. Unchanged.
- **`tribute_render_complete` NOTIFY** — same channel, same payload
  (`video_present`/`pdf_present`/`poster_present`). You still LISTEN and write
  `tributes.video_url` / `pdf_url` / `thumbnail_url`. Unchanged.
- **Output shape** — still an MP4 (in-app) + PDF (print) + poster JPEG (cover).
  Same 9:16 vertical video, same A4-portrait PDF.

**Net: `remotion` is now the agent worker's DEFAULT engine (set
`RENDER_ENGINE=legacy` to opt out) — tributes render as Flashbacks wherever the
worker's Remotion install is present, falling back to the legacy render where
it isn't. No deploy coupling with Node.**

---

## 2. Node work item A — user-facing rename to "Flashback" (optional, now)

The product no longer calls this output a "tribute" (a funeral word); it's a
**Flashback** ("send them a Flashback"). This is **copy only** at this stage:

- Update user-facing labels/strings in the tribute flow + card + share UI from
  "tribute video" → **"Flashback"**.
- **Do NOT rename** API paths, request fields, DB columns, or the NOTIFY channel
  — they stay `tribute*`. A full internal rename is a **future coordinated
  migration** (agent spec §10, Plan 5); we'll issue a separate prompt for it.

Skip this item if you'd rather do the rename all at once later — it's
independent.

---

## 3. Node work item B — control-panel "recipe" fields (the real work)

Today every Flashback renders with a **default (Friendship) recipe** because the
CRM config doesn't carry the new levers. To let an admin make occasions differ,
the **visual-theme editor** gains a `recipe` block. Node's CRM screens already
proxy visual-theme writes to the agent's `/admin/tribute_config/visual_themes`;
this adds fields to that same proxy.

> **BLOCKED / PAIRED:** the agent must first ship the config layer (migration
> 0044 adds the columns; the admin API accepts `recipe`; `/generate` snapshots
> it). Build the UI against the contract below in parallel, but the round-trip
> won't work until the agent side lands. We'll signal when it's live.

### 3a. The `recipe` contract (on a visual theme)

```jsonc
{
  // ... existing visual-theme fields (fonts, ink, audio_slug, template) ...
  "ink": { "main_fill": "#3a2c1c", "eyebrow_fill": "#96764a",
           "accent": "#e8552e" },          // NEW: accent (bold blocks/underline)
  "recipe": {
    "layout_palette": ["split_duotone", "scrapbook", "type_over_crop",
                       "fullbleed_caption"],   // allowed layouts (multi-select)
    "layout_pins": {                            // pin a layout to a structural role
      "opener":  "split_duotone",
      "payoff":  "type_over_crop",
      "closing": "fullbleed_caption"
    },
    "pacing": { "hold": 2.4, "transition": 0.7 },   // seconds
    "motion_preset": "punchy"                        // calm|playful|punchy|cinematic
  }
}
```

All fields are **optional**; omitting any degrades to the proven default (a
render never blocks on config).

### 3b. The layout catalog (enum — agent owns it)

Admins **choose from** a fixed, agent-built layout library; they never author
layouts. The slugs are part of the contract:

| slug | label (suggested) | notes |
|---|---|---|
| `split_duotone` | Split / Duotone | art one side, bold colour block + title |
| `scrapbook` | Scrapbook | overlapping polaroids + handwritten caption |
| `type_over_crop` | Big Type | giant kinetic headline over a full-bleed crop |
| `fullbleed_caption` | Full-bleed + Caption | cinematic full frame, corner caption |
| `framed_hero` | Framed (classic) | calm framed hero — the memorial default |
| `letter_note` | Handwritten letter | caption inks onto letter paper, photo under tape |
| `filmstrip` | Film strip | vertical strip slides through painted frames |
| `postcard` | Postcard | tilted vintage postcard, stamp + postmark |
| `word_mask` | Word mask | art shows through one giant word |
| `torn_reveal` | Torn paper | paper tears apart to reveal the scene |
| `gallery_wall` | Gallery wall | framed paintings + brass caption plaque |
| `magazine` | Editorial | tall art, vertical eyebrow, serif headline |
| `map_journey` | Journey map | dotted route to a pinned photo, script caption |

> The agent will expose these via a read endpoint (proposed
> `GET /flashback/layouts` → `[{slug, label, description, preview_url}]`) so your
> picker stays in sync without hardcoding. Treat the table above as provisional
> labels until that endpoint ships.

### 3c. The CRM editor controls

On the visual-theme editor, add:

- **Layout palette** — multi-select of the layout slugs (chips/checkboxes).
  Ideally show the `preview_url` thumbnail per layout.
- **Role pins** — three optional single-selects (Opener / Payoff / Closing),
  each choosing a slug **from the selected palette**. Empty = auto-sequence.
- **Pacing** — two numeric inputs, `hold` (sec, ~1.5–4) and `transition`
  (sec, ~0.3–1.2); or a friendly Fast/Medium/Slow preset mapping to those.
- **Motion preset** — single-select: `calm` / `playful` / `punchy` / `cinematic`.
  *(Forward-looking: the agent applies one motion style today; this lever
  becomes live in a later agent pass. Safe to expose now; it just won't visibly
  change output until then.)*
- **Accent** — a hex colour picker writing `ink.accent`.

### 3d. Where recipe lives / how it reaches a render

- The **visual theme** owns `recipe` + `ink.accent` (look/motion levers).
- Narrative **arc** and **voice/tone** stay on the **relationship profile**
  (existing voice/opener/art directives) — no new arc UI in this pass; a later
  prompt covers arc authoring.
- At `/generate` the agent snapshots the resolved theme's `recipe` into
  `latest_generation_context` (invariant: renders read the snapshot, so a CRM
  edit only affects a video on **manual regenerate**, exactly like today's
  fonts/ink/audio).

---

## 4. Build order / coordination

1. **Now (independent):** copy rename (§2) if you want it early.
2. **Now (parallel, against the contract):** build the CRM recipe UI (§3c) — but
   keep it behind a feature flag; the agent API won't accept `recipe` until the
   config layer merges.
3. **When agent signals config layer live:** wire the proxy to send/receive
   `recipe`, wire the `GET /flashback/layouts` picker, remove the flag.
4. **Later (separate prompt):** the full `tribute → flashback` schema/contract
   rename; arc authoring UI.

## 5. Questions for the agent team

- Confirm the final `/admin/tribute_config/visual_themes` request/response shape
  once migration 0044 lands (field names above are the intended contract).
- Confirm `GET /flashback/layouts` (or where you want the layout catalog to come
  from) so the palette picker isn't hardcoded.
- Confirm whether `ink.accent` already exists on your theme editor or is net-new.

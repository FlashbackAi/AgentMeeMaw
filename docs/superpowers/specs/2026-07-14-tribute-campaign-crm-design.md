# Tribute Campaign CRM — relationship-aware occasions, config in Postgres

**Date:** 2026-07-14 · **Deadline:** end-to-end complete by **2026-07-21**
(dry-run buffer before Friendship Day, Sun **2026-08-02**).

## 1. Problem

The Father's Day tribute shipped as code: a `Campaign` dict in
`flashback/tribute/campaigns.py`, a 22-question father-worded archetype bank
in `flashback/tribute/theme.py`, one assembler voice ("a child, a
grandchild… Meet my {relationship}") in `flashback/tribute_video/assembler.py`,
and one visual identity (page-template.jpg, sepia inks, Playfair Italic,
sentimental piano) in `flashback/tribute_video/style.py`. Every new occasion
(Friendship Day, Raksha Bandhan, Teacher's Day…) would be a PR + deploy, and
the tone would still be wrong for non-parent subjects: a friend's tribute
must not open like a eulogy.

## 2. Decisions (settled in brainstorming)

1. **Two axes.** *Occasion* (campaign) is the wrapper: title, featured
   window, message copy. *Relationship* (profile) owns tone: voice, opener
   register, art mood, question framing, visual default. A cousin tapping
   the Friendship Day campaign gets a cousin-toned video in Friendship Day
   wrapping. Any relationship, any time; occasions feature and frame.
2. **Config lives in Postgres, managed by a CRM** operated by a content
   person — no code change per occasion. Node builds the CRM screens
   (auth is Node's boundary); this service owns the tables and the admin
   API. Node never writes these tables directly.
3. **CRM UI gates the Friendship Day launch.** The campaign is created and
   published *through* the CRM as proof of the loop. No Friendship Day
   seed row.
4. **Generate-first authoring.** The content person gives a short brief;
   a big-LLM call drafts the structured fields (voice chips, opener
   examples, question bank, copy). They tune, preview, publish. Never
   generated straight to live.
5. **Structured content, not prompts.** Form fields are plain-language
   structured JSONB; a deterministic composer assembles them into fixed
   prompt slots. Guardrails (8–10-word beats, no-likeness art rules,
   fallback machinery, layout boxes) stay in code.
6. **Visual themes are config too.** Page template images are *generated*
   in the CRM (same Gemini path the renderer uses) under a hard layout
   contract; fonts and music come from curated shipped libraries. One real
   composited **sample page** is the judgment surface before publish.
7. **Relationship resolution:** synonym match against profile `synonyms`
   → else one small-LLM classify → cached on `persons.relationship_group`.
8. **Question banks:** authored bank on the friend profile (seeded, then
   CRM-tunable); campaign may pin an override bank (FD's 22); everything
   else falls back to the existing LLM-generated tribute questions seeded
   with relationship + occasion context.
9. **Friend register (explicit product note):** playful, teasing,
   partner-in-crime; never a formal "Meet my friend" introduction; sincerity
   breaks through only at the end.

## 3. Data model (migration 0039)

Three config tables, one lifecycle pattern, two column adds.

**Lifecycle (all three config tables):** `state TEXT` (`draft` |
`published` | `archived`) is the CRM lifecycle — runtime reads
`published` only. Edits use the house supersession pattern: `status`
(`active` | `superseded`), new row per edit, `version INT` increments,
partial unique index on the slug `WHERE status='active'`. `updated_by TEXT`
(Node admin identity) + timestamps on every row. Rollback = republish a
prior version as a new row.

### 3.1 `relationship_profiles`

One active row per group: `parent`, `grandparent`, `sibling`, `cousin`,
`friend`, `spouse_partner`, `mentor`, `other`.

| column | type | notes |
|---|---|---|
| `group_slug` | TEXT | registry key |
| `display_name` | TEXT | CRM label |
| `synonyms` | TEXT[] | free-text labels resolving here ("dad", "amma", "bestie") |
| `voice` | JSONB | structured: `energy_words[]`, `narrator_stance`, `emotion_rule`, `never[]` |
| `opener` | JSONB | `style` (one line), `examples[]` (with `{name}`) |
| `art` | JSONB | `mood_words[]`, `avoid[]` |
| `fallback_opener` / `fallback_closing` | TEXT | `{name}` templates for the degraded no-LLM book |
| `archetype_bank` | JSONB NULL | `[{question, options[]}]`; NULL → LLM-generated |
| `message_invitation_copy` | TEXT NULL | NULL → neutral line |
| `deage_cover` | BOOL | parents/grandparents true |
| `video_target_seconds` | INT NULL | NULL → default 45 |
| `visual_theme_id` | UUID NULL | default visual kit; NULL → built-in FD kit |

`other` is the safety floor: seeded to reproduce today's neutral behavior,
delete/archive-protected in the API.

### 3.2 `tribute_campaigns`

| column | type | notes |
|---|---|---|
| `slug`, `display_name` | TEXT | e.g. `friendship_day_2026` |
| `message_card_copy` | TEXT NULL | overrides profile copy |
| `archetype_extra_context` | TEXT | occasion framing fed to LLM question gen |
| `video_target_seconds` | INT NULL | override |
| `featured` | BOOL + `active_start`/`active_end` DATE | featuring window |
| `archetype_bank_override` | JSONB NULL | campaign-pinned bank (FD's 22 live here) |
| `deage_cover_override` | BOOL NULL | |
| `visual_theme_id` | UUID NULL | occasion-pinned kit, else profile default |

### 3.3 `tribute_visual_themes`

| column | type | notes |
|---|---|---|
| `slug`, `display_name` | TEXT | |
| `template_image` | BYTEA + `template_mime` TEXT | generated page background (≤2 MB enforced) |
| `fonts` | JSONB | `{main_slug, eyebrow_slug}` from the curated font registry |
| `ink` | JSONB | `{main_fill, eyebrow_fill}` hex |
| `audio_slug` | TEXT | from the curated track registry |

Generated candidates persist as `draft` rows; picking one publishes it,
the rest are archived. Config assets stay in Postgres (no S3 exception);
served to the CRM via an admin GET.

### 3.4 Column adds

- `persons.relationship_group TEXT NULL` — cached resolver verdict.
- `tributes.campaign_id UUID NULL REFERENCES tribute_campaigns(id) ON
  DELETE SET NULL` — stamped at tribute-row creation from the slug Node
  passes at entry; all later touchpoints read the row, not request params.

### 3.5 Seeds (in 0039)

- 8 relationship profiles with authored starting content (friend = playful
  register incl. authored friendship bank; parent = current FD tender
  register; `other` = neutral).
- FD campaign retrofitted as `published` with its June 2026 window (inert
  by date), the 22-question bank as `archetype_bank_override`,
  `deage_cover_override=true`.
- One visual theme row `classic_keepsake` wrapping the current shipped kit
  (existing page-template.jpg bytes, Playfair/Garamond, sepia inks,
  sentimental-piano) — referenced as default by all seeded profiles.

## 4. Code structure

- `flashback/tribute/relationships.py` — group constants, resolver
  (synonym → small-LLM classify → write-back), profile schema validation.
- `flashback/tribute/composer.py` — deterministic assembly of
  `{voice_block}`, `{opener_style}`, `{art_mood}` strings from structured
  JSONB. No LLM.
- `flashback/tribute/config_repository.py` — CRUD + publish + rollback +
  Valkey cache-aside (`tribute_config:*`, DEL on publish).
- `flashback/tribute/config_llm.py` — generate-first drafting call (big
  LLM, tool-forced into the structured schema).
- `flashback/tribute_video/template_gen.py` — page-template generation via
  the existing Gemini image client; prompt embeds the layout contract
  (899×1600; TEXT_BOX/ART_BOX zones calm + low-texture).
- `flashback/tribute_video/style.py` — becomes a *resolved* style: current
  constants remain as the built-in fallback kit; renderer/compositor take
  a `StyleKit` resolved from config (template bytes, fonts by slug, inks,
  audio by slug). Font/audio slug registries live here; new font files +
  2 tracks ship as package assets (playful set, clean modern set;
  upbeat-acoustic, warm-strings).
- `flashback/http/routes/admin_tribute_config.py` — the admin API.

Retired: `_CAMPAIGNS` dict + `Campaign` dataclass consumers switch to DB
(`campaigns.py` becomes a thin repository shim or is deleted);
`FATHERS_DAY_ARCHETYPE_BANK` + `build_fathers_day_archetype_questions`
move into seed data; the FD-window gate in `routes/themes.py` switches to
config-driven bank resolution.

## 5. Admin API (all under `/admin`, service-token trust, Node authenticates)

CRUD + lifecycle (uniform across the three tables):

- `GET /admin/relationship_profiles` · `GET /admin/tribute_campaigns` ·
  `GET /admin/visual_themes` (list, `include_archived` flag)
- `POST` create (as `draft`), `PUT /{id}` edit (supersedes),
  `POST /{id}/publish`, `POST /{id}/archive`,
  `POST /{id}/rollback {to_version}`
- `GET /admin/visual_themes/{id}/image` — serves the template bytea.
- `GET /admin/asset-library` — curated font + audio slugs for CRM dropdowns.

Authoring + judgment:

- `POST /admin/tribute_config/generate` — `{kind: profile|campaign,
  relationship_group?, occasion?, brief}` → structured draft fields.
- `POST /admin/visual_themes/generate` — `{brief, n_candidates≤4}` →
  draft theme rows (generated template images) + ids.
- `POST /admin/tribute_preview` — `{person_id, profile_id?|profile_draft?,
  campaign_id?|campaign_draft?, visual_theme_id?, render_sample_page?,
  sample_page_role?}` → `{book, sample_page_b64?, resolved_versions}`.
  Profile omitted → resolved from the person's relationship (same path as
  runtime), so a campaign can be previewed alone.
  Runs the real assembler over the person's real moments with draft
  config; sample page composites one page (opener default) through the
  real compositor with one generated art image. Drafts work inline —
  nothing must be saved to be tried. Light rate limit (in-process,
  per-admin-identity) on generate/preview.

Publish-time validation (code): required fields; `{name}` placeholder
present in fallback/opener examples; bank shape (≥2 options per question);
featured-window overlap → soft warning in response; template image size
cap; `other`-profile delete protection.

Public surface unchanged in shape: `GET /tribute-campaigns` now reads
published campaign rows.

## 6. Runtime resolution

1. **Entry:** Node passes the campaign slug at tribute entry;
   `tributes.campaign_id` stamped at row creation (NULL = neutral).
2. **Relationship:** `persons.relationship_group` resolved lazily on first
   tribute entry (synonym match → small-LLM → written). Re-runs only if
   cleared.
3. **Fallback chain everywhere:** campaign override → profile default →
   neutral. Applies to: archetype bank (`unlock_prepare`), message
   invitation copy, video length, de-age, visual theme.
4. **Snapshot at generate:** `/tributes/{id}/generate` resolves everything
   — composed voice/opener/art blocks, leads, visual theme id + font/track
   slugs + inks, target seconds, de-age — into `latest_generation_context`
   (per the §3 hard rule) plus config ids + versions. **The render worker
   reads only the snapshot**; template bytes are loaded by
   `visual_theme_id` at render time (id + version pinned in snapshot).
   Mid-render CRM edits cannot shift an in-flight video; regenerate
   re-resolves fresh.
5. **Safety floor:** missing/malformed config → `other` profile → built-in
   kit. A render never blocks on config.
6. **Caching:** Valkey cache-aside on config reads, DEL on publish (same
   pattern as `entity_names:*`).

## 7. Node work-order (separate doc, handed off on API freeze)

Admin-authed CRM section proxying to the admin API: campaign + profile +
visual-theme list/edit screens (generate-first forms: chips, one-line
fields, bank row editor, template candidate picker); preview panel
(person_id or legacy search → Book text + sample page image); publish with
diff-vs-live + rollback + audit list. Runtime change: pass the campaign
slug at tribute entry. `GET /tribute-campaigns` shape unchanged.

## 8. Rollout (deadline 2026-07-21)

- **Jul 14–15 (this repo):** migration 0039 + seeds; resolver + composer +
  config repository; runtime rewiring (themes gate, invitation, generate
  snapshot, render worker StyleKit). **API spec frozen Jul 15 → Node
  work-order handed off.**
- **Jul 16–17:** admin API complete (CRUD, generate, visual-theme gen,
  preview + sample page); tests green; staging deploy so Node builds
  against a running agent.
- **Jul 16–20 (Node repo, parallel):** CRM screens.
- **Jul 20–21:** end-to-end dry run — content person creates Friendship
  Day in the CRM (window ~Jul 28–Aug 3), previews, sandbox video,
  publishes. **Done Jul 21**, giving 12 days of buffer before Aug 2.
- Escape hatch (user-approved decision point, not default): if Node
  screens slip past Jul 21, the CRM-drafted config can be published via
  the admin API directly.

## 9. Testing

- Unit: resolver (synonym hit, LLM fallback, cache write-back), composer
  determinism, fallback chains (campaign→profile→neutral at every
  touchpoint), publish validation, rollback/versioning, cache
  invalidation on publish.
- Behavioral regression: parent profile + retrofitted FD campaign
  reproduces today's FD outputs (bank served whole; deage on; confession
  register in composed blocks); `other` profile reproduces today's
  neutral tribute end-to-end; degraded no-LLM book uses profile fallback
  lines (never "Meet my friend").
- Render: StyleKit resolution (config kit vs built-in fallback), snapshot
  pinning (render ignores live config), sample-page compositor parity
  with the real renderer.
- HTTP: admin CRUD lifecycle, preview with inline drafts, bytea image
  serving, `GET /tribute-campaigns` DB-backed shape.

## 10. Out of scope (v1)

- Generated music; per-beat layout editing in the CRM; campaign analytics
  dashboards; multi-language copy; Node-owned config tables (rejected —
  agent stays the write authority); auto-selecting campaigns without Node
  passing the slug at entry.

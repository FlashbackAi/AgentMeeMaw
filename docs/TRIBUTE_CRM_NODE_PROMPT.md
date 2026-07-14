# Work order: Tribute Campaign CRM (Node proxy + frontend contract)

**For:** the Node backend repo (`backend-services/legacy`).
**From:** the Python agent service. Agent-side is DONE and deployed behind
these endpoints. **Target: screens usable by 2026-07-20** so Friendship Day
(window Jul 28 – Aug 3) is created and published *through* the CRM on
Jul 20–21.

## What this is

Tribute occasion campaigns (Father's Day, Friendship Day, …) and
relationship "tone profiles" (parent / friend / cousin / …) moved from code
into Postgres config tables owned by the agent. A content person manages
them through a CRM: **generate a draft with AI → tune structured fields →
preview against a real legacy (text + one composited sample page) →
publish**. Node's job: admin-gated proxy routes + the frontend contract
doc for the CRM screens. Node never writes these tables — every write goes
through the agent admin API (same discipline as the canonical graph).

## Auth pattern (all existing machinery)

- Inbound: gate the new routes exactly like the dashboard —
  `requireDashboardAdmin` (timing-safe token compare), and add the routes
  to the `ensureAuth.unless` list in `server.js` so the dashboard admin
  token is the sole gate.
- Outbound: `agentClient.call(method, url, { body, admin: true })` — the
  existing `X-Admin-Service-Token` path.
- **Pass the admin's identity as an `X-Admin-User` header** on every
  proxied call (email or username). The agent stamps it into the config
  audit trail (`updated_by`).

## Agent endpoints to proxy (prefix them under e.g. `/api/v2/legacy/crm/*`)

`{table}` ∈ `relationship_profiles | tribute_campaigns | visual_themes`.

| Agent endpoint | Purpose |
|---|---|
| `GET /admin/tribute_config/{table}?include_archived=&include_superseded=` | List rows. `include_superseded=true` = version history for the audit/rollback view. Visual themes carry `has_image`, never bytes. |
| `POST /admin/tribute_config/{table}` body `{payload}` | Create as **draft**. 422 → `{detail: {errors: ["field: message", …]}}` — show each error next to its field. |
| `PUT /admin/tribute_config/{table}/{id}` body `{payload}` | Edit (send only changed fields). Supersedes: response `{id: <new>, version}` — **the row id changes on every edit.** |
| `POST /admin/tribute_config/{table}/{id}/publish` | Full-row re-validation, then live. Response `{warnings: []}` — surface featured-window overlap warnings non-blocking. |
| `POST /admin/tribute_config/{table}/{id}/archive` | 409 on the protected `other` profile. |
| `POST /admin/tribute_config/{table}/{id}/rollback` body `{to_row_id}` | Republish an old version's content as a new active row. |
| `GET /admin/asset-library` | `{fonts: [slug…], audio: [slug…]}` for the dropdowns. |
| `GET /admin/visual_themes/{id}/image` | Template image bytes (stream through; content-type set by agent). 404 = built-in kit, show the shipped classic template. |
| `POST /admin/tribute_config/generate` body `{kind: "profile"\|"campaign", relationship_group?, occasion?, brief}` | AI draft → `{payload, errors}`. Land `payload` in the form; NOT saved. 429 = rate limited (4/min per admin), 502 = LLM failure. |
| `POST /admin/visual_themes/generate` body `{brief, slug, display_name, n_candidates≤4, fonts?, ink?, audio_slug?}` | Generates ≤4 template candidates as **draft** rows → `{candidates: [{id, slug}]}`. Fetch each image via the image GET. 503 = Gemini key unset. |
| `POST /admin/tribute_preview` body `{person_id, profile_id?\|profile_draft?, campaign_id?\|campaign_draft?, visual_theme_id?, render_sample_page?, sample_page_role?}` | Runs the REAL assembler on that legacy → `{book: {cover_title, opener, beats[], closing, message}, resolved, sample_page_b64}`. Text-only by default; `render_sample_page: true` is a **separate button** (costs an image call). 6/min per admin. |

## One runtime change (tiny)

`POST /themes/{id}/unlock_prepare` now accepts an optional `campaign`
string (≤64 chars) in the body — forward the same campaign slug you already
put in `session_metadata.campaign`, so the question bank matches the
campaign the user tapped. Everything else (session start, generate,
progress) already passes `campaign` and is unchanged. `GET
/tribute-campaigns` keeps its exact shape (now DB-backed).

## Frontend contract (their `FRONTEND_*.md` pipeline — summarize into a doc)

Screens, in priority order:

1. **Campaign list + editor.** List (state chip: draft/published/archived,
   featured window, version). Editor is generate-first: occasion + brief →
   Generate → structured form (display name, message card copy with a
   where-this-shows hint, window date pickers, featured toggle, occasion
   context, optional bank override rows). Publish button surfaces
   `warnings`. Rollback = version history list → "restore this version".
2. **Relationship profile editor.** Same pattern. Fields: synonyms (chips),
   voice (energy chips, narrator line, emotion rule line, "never" chips),
   opener (style line + example lines — every example must contain
   `{name}`; the API 422s otherwise), art mood/avoid chips, fallback
   opener/closing lines, question bank rows (question + 4 option chips,
   add/remove/reorder), invitation copy, de-age toggle. The `other`
   profile cannot be archived.
3. **Visual theme flow.** Brief → Generate (≤4 candidate cards, images via
   the image GET) → pick one → publish it; fonts/ink/audio dropdowns from
   the asset library.
4. **Preview panel** (embed in both editors). Person picker (reuse any
   legacy search you have; a raw person_id input is fine for v1) →
   Preview → render the Book as text (cover, opener, each beat line + its
   art direction, closing, message). Separate "Render sample page" button →
   show the base64 JPEG. Previews accept the CURRENT unsaved form state via
   `profile_draft` / `campaign_draft` — tuning is live.
5. **Audit strip:** each row's `updated_by` + `updated_at` + version.

## The Friendship Day dry run (Jul 20–21, with the content person)

1. Create the `friend`-relevant campaign via generate-first
   (occasion "Friendship Day", brief along the lines of: fun, teasing,
   partner-in-crime; sincerity only at the end).
2. Preview against a real/test legacy; iterate; render the sample page.
3. Optionally generate a Friendship Day visual theme and pin it.
4. Publish with `featured: true`, window `2026-07-28` → `2026-08-03`.
5. Full video sanity check: run the normal tribute generate flow on a
   sandbox legacy and watch the render complete.

## Gotchas

- **Row ids change on every edit** (supersession). Always use the id from
  the latest response / list; never cache ids across edits.
- Draft rows are invisible to all runtime surfaces (incl.
  `/tribute-campaigns`) until published.
- Template images: bytes only ever enter via the generate endpoint; a
  `template_image` key in a CRUD payload is a 422 by design.
- Rate limits are per `X-Admin-User` — send it consistently or admins will
  share one bucket.

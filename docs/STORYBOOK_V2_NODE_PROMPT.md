# Node build prompt — v2 Standalone Storybook

> You are implementing the **Node.js Backend** side of Flashback Legacy Mode's
> v2 standalone storybook. The Python agent owns the canonical graph, the
> storybook compilation, and all writes except artifact URL columns. Node is a
> dumb worker + read surface: it reads a trigger from SQS, reads the composed
> context from Postgres, renders the PDF, writes the URLs back, and exposes a
> read endpoint for the gallery. Node has **zero** prompt/compile logic.

## Context

A **storybook** is a general keepsake book compiled from the entire legacy
memory. It is **separate from the Father's-Day tribute** (which keeps its own
video + reveal flow, unchanged). A legacy can have **many** storybook editions;
they are minted **automatically by the agent at session wrap** — there is **no
generate endpoint** on Node. All editions are kept; the UI is a newest-first
gallery.

The renderer already exists: `compiledTributeRenderer.render({kind:'storybook'})`
→ `storybookPdf.composeStorybook` (captions baked into full-bleed page images;
cover + content pages + closing card).

**Cover context (composed by the agent).** `context.cover` carries
`{ caption, subtitle, style_preset }` and, when the agent emitted a cover
concept, `{ prompt, negative }`. When `cover.prompt` is present the renderer
generates a **dedicated** dramatic cover still from it (a separate
`generateSceneStills` call); otherwise it falls back to the first content
still. The cover is laid out by `captionRenderer.renderCover` (radial vignette
+ large cursive `caption` title + muted serif `subtitle`), distinct from the
content pages' caption band. Captions/titles render via sharp's native text
API with an explicit bundled-font `fontfile` (PT Serif + Dancing Script in
`assets/fonts`), so glyphs resolve without depending on the host's fontconfig
dir scan.

## Schema you read (agent-owned, migration 0029)

`storybooks` (one row per edition):

| column | who writes | notes |
|---|---|---|
| `id, person_id, title, script, scene_moment_ids, moments_count` | agent | |
| `status` | agent | `generating \| complete \| failed \| superseded` |
| `latest_generation_context` JSONB | agent | the storybook context — **NOT keyed by kind** (read it directly) |
| `image_url, thumbnail_url` | **Node** | the only columns Node writes |
| `generation_prompt` | agent | |

`active_storybooks` view = `storybooks` minus `superseded` — your read surface.
`node_readonly` is granted `SELECT` (table + view) and `UPDATE(image_url,
thumbnail_url)` **inside migration 0029** (role-guarded). Confirm after the
migration runs:

```sql
SELECT has_table_privilege('node_readonly','public.storybooks','SELECT'),
       has_table_privilege('node_readonly','public.storybooks','UPDATE');
```

## 1. Artifact worker — new `record_type`

The agent pushes a trigger-only `artifact_generation` job:

```json
{ "job_id": "...", "record_type": "storybook", "record_id": "<storybooks.id>",
  "person_id": "...", "artifact_kind": "storybook", "source": "auto",
  "composed_at": "<iso>" }
```

In `artifactProcessor.processArtifactTrigger`, add a `record_type === 'storybook'`
branch that:

1. `SELECT latest_generation_context FROM storybooks WHERE id=$1 AND person_id=$2`.
   Missing row → skip `missing_row`. No `ctx.pages` → skip `no_context`.
2. Stale-check: `isStale(body.composed_at, ctx.composed_at)` → skip `stale_trigger`.
3. `compiledTributeRenderer.render({ kind:'storybook', context: ctx,
   tributeId: recordId, personId, userId, jobId })`.
4. `UPDATE storybooks SET image_url=$3, thumbnail_url=$4 WHERE id=$1 AND person_id=$2`.
   **Never** write `status` (agent-owned).
5. `notifyArtifactReady({ record_type:'storybook', artifact_kind:'storybook',
   image_url, video_url:null, thumbnail_url })`.

## 2. Read endpoint — the gallery

`GET /api/v2/legacy/persons/:personId/storybook` →

```json
{ "items": [ { "id", "title", "status", "ready", "momentsCount",
               "coverUrl", "thumbnailUrl", "pdfUrl", "createdAt" } ] }
```

- Read `active_storybooks WHERE person_id=$1 ORDER BY created_at DESC`.
- `ready` = derived `image_url IS NOT NULL` (status stays agent-owned; the UI
  keys off cover presence).
- `coverUrl`/`thumbnailUrl` = `toHttpUrl(image_url/thumbnail_url)`.
- `pdfUrl` = `toHttpUrl(image_url.replace('.cover.png', '.pdf'))` — the renderer
  writes the PDF at the derived key.
- **No** POST/generate route — auto-only.

## Hard rules (unchanged boundary)

- Write only `image_url` / `thumbnail_url` on `storybooks`. Nothing else.
- Never write the canonical graph or `status`.
- The trigger is a trigger; Postgres is authoritative for the context.

## Reference implementation

A working implementation lives in `backend-services/legacy`:
`service/artifactProcessor.js` (storybook branch), `model/storybooksModel.js`,
`service/readService.js` (`getStorybooks`/`mapStorybook`),
`controller/StorybookController.js`, `routes.js`. Unit tests:
`__tests__/artifactProcessor.test.js` (`processStorybookTrigger`). Port/verify
against the canonical Node repo.

## Acceptance

- A wrap that crosses the gate produces a `storybooks` row; the worker renders
  it and writes `image_url`/`thumbnail_url`; `GET .../storybook` returns it with
  a resolvable `pdfUrl`.
- `node_readonly` can SELECT + UPDATE(url cols) `storybooks` (no `permission
  denied`).
- The tribute flow is unaffected.

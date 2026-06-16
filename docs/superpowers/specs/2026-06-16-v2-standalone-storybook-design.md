# v2 Standalone Storybook — Design

**Date:** 2026-06-16
**Status:** built (agent + Node + frontend); see "Verification" below.

## Summary

A **storybook** is a general keepsake "book of memories" compiled from the
**entire legacy memory** — the same source the existing tribute storybook
artifact already represents. v2 makes it a first-class, standalone output:

- **Standalone, not a tribute.** The Father's-Day *tribute* (contributor-voiced
  video + its reveal flow) stays exactly as-is. The storybook is a separate
  thing: no contributor message, no readiness checklist, no campaign.
- **Many editions per legacy.** A legacy accumulates multiple storybooks over
  time. All editions are kept; the read surface is a newest-first gallery.
- **Auto-minted, count-gated.** New editions are generated automatically at
  Session Wrap, only when enough new qualifying moments have accumulated since
  the last edition. There is no client-side "generate" button.
- **Shares the rendering path.** Reuses the Sonnet assembler
  (`assemble_tribute_script`), the storybook context builder
  (`build_storybook_context`), and the fixed PDF renderer
  (`storybookPdf.composeStorybook`).

## Architecture

```
Session Wrap (agent)                     Node artifact worker            Frontend
─────────────────────                    ────────────────────            ────────
maybe_generate_storybook                 record_type="storybook"         /storybook
  gate: qualifying≥3 AND new≥8             SELECT latest_generation_       gallery
  assemble (Sonnet, no message)             context FROM storybooks       (GET .../storybook
  insert storybooks row ('generating')     render (kind=storybook)         → {items})
  stamp moments_at_last_storybook_run      UPDATE image_url/thumbnail_url  read-only;
  push artifact job (record_type=          (PDF at derived .pdf key)        auto-only
    storybook, artifact_kind=storybook)    notify artifact_ready
```

## Data model (migration 0029)

- **`storybooks`** table (agent owns writes; Node writes only URL columns):
  `id, person_id, title, script, scene_moment_ids, moments_count,
  status('generating'|'complete'|'failed'|'superseded'),
  image_url, thumbnail_url, generation_prompt, latest_generation_context,
  created_at, updated_at`. Context is **not keyed by kind** (unlike `tributes`)
  — this table holds storybooks only.
- **`persons.moments_at_last_storybook_run INT DEFAULT 0`** — count-gate
  watermark, mirroring `moments_at_last_thread_run`.
- **`active_storybooks`** view — Node read surface (excludes `superseded`).
- **Grants in the migration, role-guarded** — `GRANT SELECT` + `GRANT
  UPDATE (image_url, thumbnail_url)` to `node_readonly`, wrapped in a
  `pg_roles` existence check so the migration is safe on local/CI (no role) and
  prod (role present). This closes the loose-end that broke the tribute
  storybook (URL-column UPDATE never granted on a new table).

## The count-gate (agent)

`maybe_generate_storybook` runs as a best-effort step in the Session Wrap
fan-out. It no-ops unless:

- `qualifying_active_moments ≥ STORYBOOK_MIN_MOMENTS (3)`, AND
- `qualifying − persons.moments_at_last_storybook_run ≥ STORYBOOK_NEW_MOMENTS_THRESHOLD (8)`.

"Qualifying" mirrors `tribute_status` / `fetch_scene_moments`: a moment has any
of `sensory_details`, `time_anchor`, or an `involves` edge. On pass it
assembles, persists the edition + stamps the watermark in one transaction, then
pushes the trigger-only artifact job. Any failure is swallowed — a failed
storybook never fails the wrap. Timing note: at wrap, counts reflect everything
extracted through prior sessions; the gate is self-correcting, so the current
session's tail counts toward the next mint.

## Closing page

No contributor message. The assembler's `closing_caption` is promoted to the
final dark card (or `"The story of {name}"` when the fallback assembler runs).

## Node

- `artifactProcessor` gains a `record_type="storybook"` branch: read
  `storybooks.latest_generation_context`, stale-check on `composed_at`, render
  via `compiledTributeRenderer.render({kind:'storybook'})`, `UPDATE storybooks
  SET image_url, thumbnail_url`. Status is agent-owned (never written by Node).
- `GET /persons/:personId/storybook` → `{ items: [...] }` newest-first from
  `active_storybooks`. PDF URL is derived from the cover key (`.cover.png` →
  `.pdf`). No generate endpoint — generation is auto-only.

## Frontend

- `useStorybook` returns the edition list (`{items, hasAny, latest}`) and polls
  while any edition is still rendering.
- `/storybook` page is a newest-first **gallery**; each card shows cover +
  title + date + view/download PDF; in-progress editions show a "Composing…"
  card. Empty state explains editions appear as the story grows.
- `InTimeSection` storybook card reflects the latest edition's state.
- The tribute reveal flow is untouched.

## Verification

- Agent compose unit tests green (`tests/storybook/test_assemble.py`). DB-backed
  gate tests written (`tests/storybook/test_generation_db.py`) — run once the
  test container is up.
- Node: `artifactProcessor` suite green incl. 4 new storybook tests;
  readService/routes suites green.
- Frontend: `InTimeSection` + `queryKeys` suites green; typecheck clean.

## Follow-ups (not in scope)

- Cover reuses the first scene still (inherited from the tribute renderer).
- A distinct softer "storybook" visual preset (v1 reuses the default painterly
  register).
- `failed` editions currently surface as "Composing…" (no failure write path
  exists yet).

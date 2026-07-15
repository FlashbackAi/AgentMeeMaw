# CRM frontend — contract corrections (verified against the agent code)

**Paste this into the dashboard-repo Claude Code chat** (the one that built
the CRM screens from the Node team's frontend prompt). The agent service is
the source of truth; these were verified line-by-line against the deployed
implementation on 2026-07-14. Two real fixes, three awareness notes.

---

## Fix 1 — List responses are `{"rows": [...]}`, NOT a bare array

`GET /api/v2/legacy/crm/tribute_config/{table}` returns:

```json
{ "rows": [ { "id": "...", "slug": "...", "state": "published", ... } ] }
```

The frontend prompt's default type (`ListResponse = ConfigRow[]`) is wrong —
its own hedge line ("if the deployed agent wraps the list in an object,
adjust ListResponse — one place") applies. Read `data.rows`.

**This is almost certainly why the Campaigns and Profiles screens show
"No rows yet"** — the database ships pre-seeded with 8 relationship
profiles and the `fathers_day_2026` campaign. Confirm in the Network tab:

- body is `{"rows":[ …items… ]}` → this frontend bug; fix and the seeded
  rows appear.
- body is `{"rows":[]}` → the environment's DB hasn't run agent migration
  0039 — tell the backend team; no frontend change needed.

## Fix 2 — Preview `opener` / `closing` are objects, not strings

`POST /tribute_preview` → `book.opener` and `book.closing` have the SAME
shape as each beat:

```json
{ "line": "Nobody warned me about Arjun.", "art_direction": "…", "moment_id": "" }
```

The frontend prompt typed them as `string` — rendered as-is they show
`[object Object]`. Corrected types:

```ts
export interface PreviewBeat {
  line: string;
  art_direction: string;
  moment_id: string;
}
export interface PreviewResponse {
  book: {
    cover_title: string;
    opener: PreviewBeat;      // was string — FIX
    beats: PreviewBeat[];
    closing: PreviewBeat;     // was string — FIX
    message: string;
  };
  resolved: Record<string, unknown>;
  sample_page_b64?: string;
}
```

Render `opener.line` as the text; `art_direction` is the picture
description (show it the same way beats show theirs).

## Notes (no code change required)

1. **Theme candidate slugs are suffixed.** Generating with slug
   `friendship_day` creates draft rows `friendship_day_c1` … `_c4`
   (slug uniqueness applies across drafts). The response
   `{candidates: [{id, slug}]}` already carries the suffixed slug.
2. **Preview can also 503** when "Render sample page" is clicked and the
   agent has no image-model key configured — same handling as the theme
   generate 503 ("Image generation is not configured").
3. Preview also accepts `sample_page_role: "opener" | "beat" | "closing"`
   plus `sample_beat_index` (0–15) — optional, only needed for a future
   "render this specific beat" picker.

Everything else in the frontend prompt is confirmed accurate: 422
`{detail:{errors:["field: message"]}}`, id-changes-on-every-edit
(`PUT → {id, version}`), publish `{warnings:[]}`, rollback `{to_row_id}`,
`{fonts, audio}` asset library, binary image GET with 404 = built-in
template, rate limits (generate 4/min, preview 6/min, per admin).

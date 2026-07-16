# CRM rows: edit + delete (themes, campaigns, profiles)

Ask: the Existing Themes list is append-only junk — no way to rename a
draft, no way to get rid of abandoned candidates (`test1` ×4,
`marvel themed 5` ×4…). Make rows **editable** and **deletable**.

Agent-side is DONE (needs the next agent redeploy). This doc is the Node
half (one proxy route) and the dashboard half (row actions). Everything
below applies to all three tables — themes, campaigns, profiles — the
screenshots' pain is just loudest on themes.

## Agent surface (what Node proxies to)

| action | endpoint | notes |
|---|---|---|
| edit | `PUT /admin/tribute_config/{table}/{id}` body `{payload: {...changed fields}}` | already existed. Supersession: returns `{id: NEW_ID, version}` — the row you edited is gone from the active list, the NEW id replaces it. Partial payloads fine; unchanged fields carry over (a theme's template image is kept automatically — it is NOT editable, regenerate for a new look). |
| delete | `DELETE /admin/tribute_config/{table}/{id}` — **NEW** | hard-deletes a **never-published draft** and its whole edit chain; frees the slug for reuse; drops stored template bytes. Returns `{id, deleted_rows}`. `409` if the row is published/archived (detail says "archive it instead"); `404` unknown/malformed id. |
| archive | `POST /admin/tribute_config/{table}/{id}/archive` | already existed — the "delete" for anything that has been published. Archived rows vanish from the default list (`include_archived=true` to see them). `409` on the protected `other` profile. |

Draft → **Delete** (really gone). Published → **Archive** (hidden, history
kept — render snapshots may pin it). That split is enforced server-side;
the UI just picks the right verb per row state.

## Node (backend-services/legacy) — one passthrough route

Mirror of `crmArchiveConfig`, verb DELETE, no body:

`service/agentClient.js`:

```js
// DELETE /admin/tribute_config/{table}/{id} — hard-delete a never-published
// draft chain; 409 (forwarded verbatim) when the row was published.
async function crmDeleteConfig({ table, id, adminUser }) {
  return callAdminPassthrough(
    'DELETE',
    `/admin/tribute_config/${encodeURIComponent(table)}/${encodeURIComponent(id)}`,
    { adminUser },
  );
}
```

`controller/CrmController.js` (mirror `archiveConfig`):

```js
async function deleteConfig(req, res) {
  const table = tableOrReject(req, res);
  if (!table) return undefined;
  try {
    const out = await agentClient.crmDeleteConfig({ table, id: req.params.id, adminUser: adminUser(req) });
    return forward(res, out);
  } catch (err) {
    return transportError(res, 'delete', err);
  }
}
```

`routes.js` (next to the archive line):

```js
crmRouter.delete('/tribute_config/:table/:id', CrmController.deleteConfig);
```

Export both new functions. Status + body forward verbatim as everywhere
else (D113).

## Dashboard (flashback_agent_admin) — row actions

On every row of the Existing Themes list (and the campaigns/profiles
lists — same component, same rules):

### 1. Edit (pencil icon, all active rows)

Opens a modal prefilled from the row. For themes the editable fields are:
`display_name`, `slug`, `fonts` (two selects off `/asset-library`), `ink`
(two color inputs), `audio_slug` (select). The template image is shown
read-only with the caption "To change the artwork, generate a new theme."
For campaigns/profiles, reuse the existing editor form — this is the same
PUT the editors already call.

Save → `PUT /api/v2/legacy/crm/tribute_config/{table}/{id}` with only the
changed fields in `payload`. **The response carries a NEW `id`** (edits
supersede) — replace the row in state with the new id/version; don't keep
the old id anywhere. 422 renders per-field as usual
(`detail.errors: ["field: message"]`).

### 2. Delete (trash icon, DRAFT rows only)

Confirm dialog: "Delete draft '{display_name}'? This deletes the draft
and its history for good and frees the slug." →
`DELETE /api/v2/legacy/crm/tribute_config/{table}/{id}` → remove the row.
On `409`, toast the response detail and offer Archive instead (state can
race: someone published it meanwhile).

### 3. Archive (box icon, PUBLISHED rows)

Confirm: "Archive '{display_name}'? Videos keep working; it just stops
being available for new attachments and leaves this list." →
existing `POST .../archive` → remove the row from the list (the default
list already excludes archived). `409` on the `other` profile — toast the
detail.

### 4. Bulk cleanup nicety (optional)

With 4-candidate generation, junk accumulates in fours. A checkbox
multi-select on DRAFT rows + one "Delete selected" button (sequential
DELETEs) turns the screenshot's 12 junk rows into 3 clicks.

## Acceptance

- [ ] Every draft row shows Edit + Delete; every published row shows
      Edit + Archive.
- [ ] Edit modal PUTs changed fields only and swaps in the returned new id.
- [ ] Deleting a draft removes it and its slug is immediately reusable.
- [ ] Deleting something published is impossible in the UI (only Archive
      offered) and a raced 409 shows the server's message.
- [ ] Node forwards agent status + body verbatim for the new DELETE.

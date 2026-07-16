# CRM dashboard live audit — 2026-07-16 (Playwright, against prod)

Drove localhost:5173 → prod Node → prod agent end-to-end as the admin
user. Every network call in the session returned **200**; zero console
errors/warnings. The agent redeploy + `migrate up` (0040) are confirmed
live.

## What was exercised and PASSES

| flow | result |
|---|---|
| Theme generate (1 candidate, playful brief) | ~40s, border comes out genuinely playful — the new brief-led prompt is live |
| Zone overlays (TEXT/ART) + toggle + zoom | rendering on candidates |
| Candidate Select → Publish selected | 200; theme listed `published` |
| Campaign create → Save draft → Publish (confirm dialog) | 200 → 200 |
| Campaign edit Save (PUT) | supersession works; friendship campaign now **v5, featured=true, window Jul 16 → Aug 3** (verified in DB) |
| Visual-theme dropdown + thumbnail on campaign editor | attach works; ids handled invisibly |
| **Delete** draft (campaign `audit_delete_1`) | true hard delete — chain purged from prod DB, slug freed |
| Restore version (rollback v2 → v1 content) | 200; v1 content republished |
| Archive (with confirm) | 200; row leaves the default list |
| Preview (text-only) | real Book assembled from the person's moments |
| `tribute_status` campaign columns (0040) | present in prod |

Audit residue: theme **"Audit Playful"** (published — a decent playful
border made with the new prompt; feel free to attach it to the
friendship campaign or archive it) and campaign **audit_campaign_1**
(archived). `audit_delete_1` is fully deleted.

## Findings (all frontend — backend held up everywhere)

### F1 — the reported "publish is not working" (root cause found)

The moment any field changes, the **Publish button silently disables**
with only a hover tooltip ("Save before publishing."). The user toggles
Featured → presses Publish → nothing happens. Worse, for an
already-published row Publish is never needed — **Save is what makes
edits live** (the new version carries the published state).

Fix options (pick one):
- On published rows, hide Publish and make the primary button
  **"Save changes (goes live)"**.
- Or when dirty, keep Publish enabled and make it save-then-publish.
- At minimum: show the "Save before publishing" hint as inline text
  next to the button, not a hover title.

### F2 — Preview ignores the selected campaign

Previewing with the friend-day campaign OPEN in the editor sent
`/tribute_preview` with no campaign — the result banner read
"parent profile × **no campaign**". The content person can never see
campaign copy/context effects. Fix: pass `campaign_id` of the selected
row (or `campaign_draft` when the form is dirty) in the preview body.
The agent already supports both fields.

### F3 — after Restore, the editor deselects

Restoring a version rolls back correctly but clears the TUNE panel to
new-campaign mode instead of loading the restored row. Reload the
restored row into the editor (the rollback response carries its new id).

## Operator notes

- The friendship campaign is now correctly configured (featured, window
  covers Aug 2). Remaining cosmetic: slug typo `frinedship_day_2026`.
- `/tribute-campaigns` should now report `is_active: true` +
  `active_featured_slug` for it.
- The old FD-looking video: regenerate AFTER attaching the desired
  playful theme to the campaign — the agent now keeps the campaign skin
  on regenerate and paints with the profile's mood.

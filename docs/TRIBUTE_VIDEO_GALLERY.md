# Tribute video gallery — one video per campaign, none of them vanish

Prod report (2026-07-16): generating the friend-day tribute made the
Father's Day video "disappear". It didn't — prod has FOUR completed
tribute rows for that person, each with its own `video_url` — but the
consumer app renders exactly one video, so every new campaign render
LOOKS like it replaced the last one.

Agent-side is DONE (migration 0040 + campaign-scoped tribute lifecycles;
needs the next agent redeploy + `migrate up`). This doc is the Node half
(tiny read change) and the consumer-frontend half (the gallery).

## The model (after agent redeploy)

- Each campaign entry gets its OWN tribute row: tapping the Friendship
  Day card never reopens the completed FD tribute; it opens/creates the
  friendship one (archetype answers carry over via the theme, so the
  meter starts warm — that's the "70%" you see).
- A completed tribute is immutable from other campaigns. Regenerate/edit
  still re-render THEIR OWN row only.
- `tribute_status` now carries `campaign_id`, `campaign_slug`,
  `campaign_display_name` (NULL for pre-campaign tributes) so every row
  is labelable.

## Node (backend-services/legacy)

`model/tributesModel.js` — `getStatus` already returns ALL rows for the
person; add the three new columns to the SELECT:

```sql
SELECT id, person_id, theme_id, status,
       campaign_id, campaign_slug, campaign_display_name,
       memories_count, message_present, appearance_present, signature_present,
       percent, ready,
       video_url, image_url, thumbnail_url, pdf_url, rendered_at
FROM   tribute_status
WHERE  person_id = $1
ORDER  BY created_at DESC
```

(Also switch the `ORDER BY id ASC` to `created_at DESC` — the FE wants
newest first.) Map them in `readService.mapTributeStatus` as
`campaignId`, `campaignSlug`, `campaignTitle`.

## Consumer frontend

1. **The active card** binds to the OPEN tribute (status
   draft/ready/generating) — there is at most one per campaign. Keep the
   current meter/message-card/Generate behavior on it.
2. **The gallery**: below (or behind "All videos"), list every
   `status='complete'` item — thumbnail, title
   (`campaignTitle || "Tribute"`), rendered date, play + download PDF.
   The FD video and the friendship video are separate entries forever.
3. **Label the in-progress card too**: "A friend day tribute — 70%"
   beats a bare meter when more than one campaign is live.

## Two config notes for the CRM operator (not code)

- The live friendship campaign's slug is `frinedship_day_2026`
  (typo'd), `featured=false`, window `2026-07-16 → 2026-07-30`.
  Friendship Day is **Aug 2** — the window misses the day itself. Edit
  the campaign: set featured, window `2026-07-28 → 2026-08-03`. (Slug
  fixes mint a new row id; the agent repoints open tributes
  automatically.)
- Its attached visual theme is the ornate `test1` (generated under the
  old FD-register prompt) — that's why the video's border reads
  Father's Day. After the agent redeploy, generate fresh candidates
  with a playful brief and attach the new published theme to the
  campaign.

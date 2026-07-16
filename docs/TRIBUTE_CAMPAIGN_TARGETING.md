# Campaign relationship targeting — "not every campaign for every profile"

Ask (2026-07-16): a father legacy was getting the Friendship Day campaign
card — and a targeted campaign's question-bank override even replaced the
parent profile's questions. Campaigns can now be scoped to relationship
groups.

**Agent-side is DONE** (migration 0041 + enforcement). Needs the next
agent redeploy + `migrate up`. This doc is the Node line and the
dashboard control.

## How it works

`tribute_campaigns.relationship_groups TEXT[]` — the profile group slugs
the campaign applies to. **Empty = all relationships** (default; every
existing campaign keeps today's behavior until edited).

Enforced everywhere a campaign resolves for a person:

| surface | behavior for a non-matching legacy |
|---|---|
| `unlock_prepare` (tribute card) | campaign skin + bank override ignored → neutral card, profile's own questions |
| `/session/start` campaign stamping | campaign not stamped, not in Working Memory (no campaign copy) |
| render config (`/generate`, regenerate, edit) | campaign degrades to neutral; profile still owns tone + theme |
| `GET /tribute-campaigns?person_id=...` | `is_active=false` for that person; never `active_featured_slug` |

An **unclassified** legacy (relationship group not yet resolved) never
gets a targeted campaign — targeting fails closed.

## Node — one query param

`GET /persons/:personId/tribute-campaigns` proxies the agent's
`GET /tribute-campaigns`. Forward the person: call it as
`/tribute-campaigns?person_id=<personId>`. That makes `is_active` and
`active_featured_slug` per-person, which is what the consumer app should
key the campaign card off. (Without the param the agent keeps the old
global behavior, so this is non-breaking either way.)

The CRM CRUD proxy needs nothing — `relationship_groups` rides the
payload passthrough.

## Dashboard — one field on the campaign editor

Add **"Relationships"** (multi-select chips) to the campaign TUNE panel:

- Options = the relationship profiles' `group_slug`s (fetch from
  `GET /crm/tribute_config/relationship_profiles`, active rows), plus a
  default state **"All relationships"** (= empty list / omitted).
- Bind to payload key `relationship_groups` (list of strings; move out
  of ExtraFields into the known keys).
- Helper text: "Which relationships see this campaign. Leave on All for
  a universal occasion; pick friend + cousin for Friendship Day so a
  father's legacy never gets the friendship card."
- The list endpoint already returns `relationship_groups` per campaign —
  show chips on the campaign row too.
- 422 shape is the usual `detail.errors` (`"relationship_groups: ..."`).

## Suggested config for the live campaigns

- `frinedship_day_2026` → `["friend", "cousin"]` (or just friend)
- `fathers_day_2026` → `["parent"]` (its window is over, but for next year)

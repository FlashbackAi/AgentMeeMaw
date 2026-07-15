# CRM field tooltips — "?" beside every field (paste into the dashboard repo)

Add a small **?** icon after each field label across the CRM screens. All
tooltip copy below is final — apply verbatim. It's written for a content
person with zero engineering context: every entry says what the field is
AND what happens downstream when they change it.

## Component spec (one shared `<FieldHelp>` component)

- A `?` glyph after the label text: muted color, 14px circle, cursor help.
- Opens on hover AND on click/tap (mobile + accessibility); closes on
  Escape / outside click. `tabIndex=0`, `aria-label="What is this field?"`,
  tooltip body in `role="tooltip"` linked via `aria-describedby`.
- Max width ~300px, matches the existing dark panel styling (background
  slightly lighter than the panel, 1px hairline border, 12–13px text).
- No library — a positioned div is fine. Usage:
  `<label>Display name <FieldHelp text={HELP.campaign.display_name} /></label>`
- Keep the copy in ONE module, e.g. `src/crm/fieldHelp.ts`, exported as a
  nested object mirroring the tables below (`HELP.campaign.slug`, …).
- If a field already has a visible `field-hint` line, keep the hint SHORT
  and put the fuller text in the `?` — don't show the same sentence twice.

---

## Campaigns screen (`HELP.campaign.*`)

| key | Tooltip text |
|---|---|
| `slug` | Permanent internal ID, e.g. `friendship_day_2026`. The app and analytics refer to the campaign by this forever — set it once and never reuse an old one. Users never see it. |
| `display_name` | The title users see on the tribute card while this campaign is active, e.g. "A Friendship Day Tribute". |
| `message_card_copy` | The question shown on the "say one thing straight to them" card during this campaign — in chat and on the tribute card. While the campaign applies, this overrides each relationship profile's own version. Leave empty to keep the per-relationship wording. |
| `window_start` / `window_end` | Between these dates the app features this campaign. Outside them it goes dormant automatically — there is nothing to turn off afterwards. |
| `featured` | On = the app surfaces this campaign between the window dates. Off = the campaign exists (links to it still work) but the app never promotes it. |
| `occasion_context` | 1–2 sentences ONLY the AI reads, e.g. "This is a Friendship Day tribute — frame around shared laughter, loyalty, years of friendship." It shapes the questions and framing for relationships that don't have hand-written questions. Users never see this text. |
| `bank_override` | ⚠️ The big hammer. When on, EVERY relationship (father, friend, cousin…) gets this exact question set during the campaign, instead of their own. Father's Day uses this (its 22 questions). Leave OFF for occasions like Friendship Day, where each relationship should keep its own toned questions. |
| `save_draft` | Saves without going live. Drafts are invisible to users everywhere — take your time. |
| `publish` | Goes live immediately for NEW tributes. Videos already made never change; someone mid-flow keeps what they started with. You can archive or roll back any time — nothing here is destructive. |

## Relationship profiles screen (`HELP.profile.*`)

| key | Tooltip text |
|---|---|
| `group_slug` | Which relationship this profile is. The 8 built-ins (parent, grandparent, sibling, cousin, friend, spouse/partner, mentor, other) cover everyone — you tune them, you almost never create new ones. |
| `display_name` | Label for this profile in the CRM and in preview readouts. Users don't see it. |
| `synonyms` | The words users type that mean this relationship — "dad", "amma", "bestie", "yaar". When a user's label matches one, their videos get this profile's tone. Add regional terms here when someone lands in the wrong bucket. Unknown labels are classified by AI, then fall back to "other". |
| `voice_energy` | 3–5 mood words for every line the AI writes in the video — e.g. "playful, teasing, loyal" for friends, "tender, admiring" for parents. This is the single strongest lever on how the video FEELS. |
| `narrator_line` | Who is telling the story? One line, e.g. "their partner-in-crime telling the stories". The AI writes the whole video in this person's shoes. |
| `emotion_rule` | Where the feeling lives, e.g. "warmth hides inside the jokes; sincerity only breaks through at the very end". Controls the emotional arc across the video's pages. |
| `never_say` | Hard bans. Anything listed here will not appear in any video for this relationship — e.g. "meet my friend introductions", "eulogy tone". |
| `opener_style` | How the video's FIRST line should read — a direction, not a script: "open like the first line of a story told at every party". |
| `opener_examples` | 2–3 example first lines in the right register. Must contain `{name}` — it becomes the person's real name. The AI imitates the STYLE, it never copies these word-for-word. |
| `art_mood` | Mood words for every illustration in the video: "bright, candid, mid-motion" paints very different pictures than "sepia warmth, oil lamps". |
| `art_avoid` | Visual bans — e.g. "posed, solemn, candlelight" keeps a friend video from looking like a memorial. |
| `fallback_opener` / `fallback_closing` | Safety copy used ONLY if the AI writer fails mid-render, so even a degraded video opens and closes in the right register. Must contain `{name}`. |
| `question_bank` | The pre-chat questions users answer for this relationship. Leave EMPTY and the AI writes fresh, personalised questions per user (fine for most). Hand-write them when the arc matters — like the friend profile's playful set. Each question needs at least 2 options. |
| `invitation_copy` | This relationship's version of the "say one thing straight to them" question. A live campaign's message copy overrides it while that campaign applies. |
| `deage_cover` | On = the cover portrait is painted at their prime years (right for parents/grandparents whose photos are from old age). Off for friends and peers — their photos are already the right era. |
| `publish` | Applies to every FUTURE video for this relationship, product-wide, immediately. Existing videos never change; users pick the new tone up on their next generate. Always Preview first. |
| `archive` | Retires this profile: new videos for the relationship fall back to the neutral "other" tone until you publish a replacement. Nothing is deleted — re-publish to bring it back. The "other" profile can't be archived; it's the safety net. |

## Visual themes screen (`HELP.theme.*`)

| key | Tooltip text |
|---|---|
| `brief` | Describe the page look in plain words — "warm doodle border, polaroid corners, friendship-bracelet motif". The AI paints up to 4 background candidates from this. |
| `slug` / `display_name` | Internal ID + label. Candidates get numbered slugs automatically (`_c1`…`_c4`). |
| `fonts` | The lettering on every page: main = the big emotional line, eyebrow = small caps accents. Choices come from the curated library. |
| `ink` | Text colors (hex). Dark sepia reads classic; try brighter inks for playful themes — but check the sample page for legibility. |
| `audio_slug` | The music bed under the video. The library is curated — ask engineering to add licensed tracks; new files are a two-line change. |
| `generate` | Paints up to 4 template candidates as drafts (slow — a few minutes). Pick the one that works and publish it; the rest stay invisible drafts. |
| `publish` | Makes the theme available to point campaigns/profiles at. Publishing alone changes nothing until something references it. |

## Preview panel (`HELP.preview.*`)

| key | Tooltip text |
|---|---|
| `person_id` | Any legacy's person ID — grab one from the Ops dashboard or ask an engineer. Preview builds the REAL video script from that person's actual saved memories, so pick a test legacy with some material. |
| `preview` | Runs the real AI writer (~30–60s, one AI call). Shows the full script: cover title, opening line, every page's line + its picture description, closing, and the message card. This is exactly what would be rendered. |
| `render_sample_page` | Additionally paints ONE real page image (slower, costs an image-generation call). Use it before publishing; no need on every tweak. |
| `resolved` | Which relationship profile × campaign the preview used — confirms you're looking at the combination you think you are. |

## List screens (`HELP.list.*`)

| key | Tooltip text |
|---|---|
| `state_chip` | draft = only visible here, users never see it · published = live for new tributes · archived = retired, recoverable by re-publishing. |
| `version` | Every save is a new version — full history is kept, and any old version can be restored. You cannot permanently lose work here. |
| `updated_by` | Who last changed this row, and when — the audit trail. |
| `show_archived` | Include retired rows so you can inspect or re-publish them. |

---

### Acceptance

- [ ] Every labeled field on all three editors + preview panel has a `?`.
- [ ] Tooltips open on hover AND click, close on Escape/outside, reachable
      by keyboard.
- [ ] Copy matches this doc verbatim (single source: `fieldHelp.ts`).
- [ ] No duplicated text where a visible field-hint already exists.

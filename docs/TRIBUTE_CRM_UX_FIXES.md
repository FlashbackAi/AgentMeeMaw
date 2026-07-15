# CRM frontend — P0 contract bug + understandability pass

**Paste into the dashboard-repo Claude Code chat.** Verified against the
agent implementation (source of truth) on 2026-07-15. One data-loss bug,
then a copy/UX layer that makes the screens self-explanatory for a
content person. Apply in order.

---

## P0 — Campaign form binds the WRONG payload keys (data loss + empty Generate)

`src/crm/payloadKeys.ts` `KNOWN_CAMPAIGN_KEYS` and the bindings in
`src/crm/CampaignEditor.tsx` use five keys the agent does not have:

| Form uses (wrong) | Agent expects (fix to this) |
|---|---|
| `message_card` | `message_card_copy` |
| `window_start` | `active_start` |
| `window_end` | `active_end` |
| `occasion_context` | `archetype_extra_context` |
| `question_bank` | `archetype_bank_override` |

Verified agent-side: generate returns `message_card_copy` /
`archetype_extra_context` / `archetype_bank_override` (required keys in its
tool schema), and CRUD accepts only
`slug, display_name, message_card_copy, archetype_extra_context,
video_target_seconds, featured, active_start, active_end,
archetype_bank_override, deage_cover_override, visual_theme_id,
closing_card_copy` — unknown keys are **silently dropped** on create/edit.

**Symptoms this explains (all currently live):**
1. Click **Generate** → every structured field stays empty; the draft lands
   in the raw ExtraFields JSON blob. Looks broken.
2. Fill the form manually → Save → the agent silently drops
   `message_card`/`window_start`/… → the saved campaign has **no copy and
   no window** → Publish with Featured fails
   `422 "featured: a featured campaign needs a window"` — an error keyed to
   `featured`/`active_start`, i.e. fields the form can't even mark.

Fix = rename in `payloadKeys.ts` + the five `CampaignEditor.tsx` bindings
(+ date inputs read/write `active_start`/`active_end`; ISO `YYYY-MM-DD`
strings are what the agent accepts). Profile keys are already correct —
don't touch them. Update the campaign tests' fixtures accordingly.

Also fix the misleading hint under message card copy — it is NOT rendered
in the video outro. Correct hint: “The prompt on the ‘say one thing to
them’ card during the tribute chat. Overrides the relationship profile's
version while this campaign applies.”

---

## P1 — Make the screens explain themselves (exact copy provided)

The operator is a content person with zero context. Every screen needs to
answer: what is this, what happens when I touch it. All copy below is
final — apply verbatim.

### 1. Per-screen explainer (one collapsible panel at the top of each)

**/crm/campaigns:**
> **Campaigns are occasions.** A campaign wraps tributes in an occasion
> (Friendship Day, Raksha Bandhan…) between two dates: the app features it,
> and its copy overrides the relationship profile's while active. It never
> changes *who* the video is about or its tone — that comes from the
> Relationship Profile. Flow: **Generate → tune → Save draft → Preview →
> Publish.** Drafts are invisible to users. Outside its window a published
> campaign goes dormant by itself.

**/crm/profiles:**
> **Profiles are tone.** One per relationship kind (parent, friend,
> cousin…). They control how every tribute video for that relationship
> *sounds and looks* — year-round, occasion or not. The 8 built-in profiles
> are already written; you tune them, you rarely create new ones.
> **Editing a published profile changes every future video for that
> relationship** — always Preview before Publish. Existing videos never
> change.

**/crm/themes:**
> **Themes are the page look.** The paper the video's pages are printed
> on: background art, fonts, ink color, music. Generate up to 4 background
> candidates from a brief, pick one, publish it. A campaign or profile can
> then point at it.

### 2. Field hints — campaigns (add `field-hint` under each)

| Field | Hint |
|---|---|
| Slug | Permanent internal id, e.g. `friendship_day_2026`. Set once, never reuse. |
| Display name | The title users see on the tribute card. |
| Message card copy | (see corrected hint in P0) |
| Window start/end | The app features this campaign between these dates — scheduling is automatic, nothing to turn off. |
| Featured during window | Off = the campaign exists but the app never surfaces it. On = it's the featured tribute between the window dates. |
| Occasion context (for the AI) | 1–2 sentences the AI reads when writing questions and framing, e.g. “This is a Friendship Day tribute — frame around shared laughter, loyalty, years of friendship.” Users never see this text. |
| Override question bank | ⚠️ Replaces the pre-chat questions for **every relationship** during this campaign. Leave OFF unless the occasion demands one fixed set (Father's Day does; Friendship Day should not — the friend profile already has playful questions). |

### 3. Field hints — profiles

| Field | Hint |
|---|---|
| Synonyms | Labels users type that mean this relationship (“dad”, “amma”, “bestie”). Add regional terms when one lands in the wrong bucket. |
| Voice energy | 3–5 words for the mood of every line the AI writes (“playful, teasing, loyal”). |
| Narrator line | Who is telling the story? (“their partner-in-crime telling the stories”). |
| Emotion rule | Where the feeling lives (“warmth hides inside the jokes; sincerity only at the very end”). |
| Never say | Hard bans. Anything here will not appear in videos (“meet my friend introductions”). |
| Opener style + examples | How the video's FIRST line reads. Examples must contain `{name}` — it becomes the person's real name. |
| Art mood / avoid | Steers every illustration: mood words in, avoid-words banned (“bright, candid” / “posed, solemn”). |
| Fallback opener/closing | Only used if the AI writer fails — the safety copy. Needs `{name}`. |
| Question bank | The pre-chat questions for this relationship. Empty = the AI writes them fresh per person (fine). Each question needs ≥2 options. |
| Invitation copy | The “say one thing straight to them” card in the chat. |
| De-age in art | Paint the cover portrait at their prime years — on for parents/grandparents, off for friends and peers. |

### 4. Preview panel upgrades (both editors)

- Render `result.resolved` (currently fetched and DROPPED) above the book:
  “Previewed as: **{resolved.group_slug} profile** × **{campaign
  display_name or 'no campaign'}**”. This is the single best “what am I
  looking at” signal the panel can show.
- Person ID hint under the input: “Any legacy's person_id — grab one from
  the Ops dashboard or ask an engineer. The preview reads that person's
  real saved memories.”
- Remember the last-used person_id in localStorage; prefill it.
- Under **Preview**: “Free-ish: text only (one AI call, ~30–60s).” Under
  **Render sample page**: “Paints one real page image — slower, costs an
  image call.”

### 5. Publish confirmation (replace bare publish click)

Confirm dialog copy — campaigns:
> Publish “{display_name}”? It goes live immediately: featured in the app
> {active_start}–{active_end}, and its copy applies to new tributes.
> Existing and in-progress videos never change. You can archive or roll
> back any time.

Profiles:
> Publish changes to the **{display_name}** profile? Every FUTURE
> {display_name}-relationship video uses this immediately. Existing videos
> don't change; users get the new tone on their next generate.

### 6. Empty/seeded states

- Profiles list empty-state is currently “No rows yet — generate one to
  get started”, which is wrong: **8 built-in profiles always exist**. If
  the list is empty the environment is broken — copy: “Couldn't load the
  built-in profiles — check that the backend has run migration 0039 /
  see Network tab.” (And confirm the list code reads `data.rows`.)
- Campaigns empty-state: “No campaigns yet. Generate your first one —
  e.g. occasion ‘Friendship Day’, brief: ‘fun, teasing, partner-in-crime;
  sincerity only at the end.’”
- GeneratePanel placeholders: occasion input `Friendship Day`; brief
  textarea `fun, teasing, partner-in-crime energy; sincere only at the
  very end`.

### 7. Small but load-bearing

- Section labels `GENERATE DRAFT` / `EDITOR` / `PREVIEW` → keep, but add a
  step number: `1 · GENERATE`, `2 · TUNE`, `3 · PREVIEW`, `4 · PUBLISH`
  (publish number sits on the button row).
- Disable **Publish** until the row has been saved at least once, with
  tooltip “Save draft first.”
- After Generate succeeds, scroll/focus the editor and toast “Draft
  generated — review every field, then Save draft.”

---

## Verification checklist (after applying)

- [ ] Generate a campaign → all structured fields populate (no content in
      the ExtraFields JSON section).
- [ ] Save → re-open the row → copy + window survived (they round-trip).
- [ ] Publish with Featured + window → succeeds; without window → the 422
      lands on the window fields inline.
- [ ] Preview shows the resolved profile × campaign line.
- [ ] Profiles list shows the 8 built-ins (after backend migration 0039).

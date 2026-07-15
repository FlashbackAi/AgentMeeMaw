# Themes screen — make candidate picking actually usable

**Paste into the dashboard-repo Claude Code chat.** Context: the first live
generation round produced busy comic borders with visible band/seam
artifacts across the interior, and the picker gives the content person no
way to judge whether a candidate will compose legibly.

**Agent-side is already fixed** (better generation prompt: border-only
decoration, one continuous interior, seam/band artifacts hard-negated,
palette discipline) — after the next agent deploy, regenerate; archive the
old `test_c*` drafts. The items below are the FRONTEND half.

## 1. Zone overlay on every candidate (the killer feature)

The whole judgment is "will text and art land on clean paper?" — so draw
the zones on the candidate images. The compositor's layout uses these
fractional rectangles of the image (x0, y0, x1, y1 as fractions of
width/height):

```ts
export const TEXT_ZONE = { x0: 0.10, y0: 0.205, x1: 0.90, y1: 0.45 };
export const ART_ZONE  = { x0: 0.03, y0: 0.47,  x1: 0.97, y1: 0.985 };
```

Render each candidate with two labeled translucent overlays ("TEXT",
"ART") — dashed 1px outline, ~8% tinted fill, small corner label. Toggle
("Show zones", default ON). If the border art intrudes into either
rectangle, the content person sees it instantly and skips that candidate.

## 2. Click to zoom

Candidates are unjudgeable at thumbnail size. Click → lightbox at natural
size (the images are 9:16, ~900×1600), zones overlay included, ←/→ to flip
between candidates, Esc closes.

## 3. Teach the brief (placeholder + helper)

The brief describes the BORDER only — the interior always stays plain
paper (the agent enforces this now). Set the textarea placeholder to:

> comic-book border with halftone dots and action bursts, red + gold on
> warm cream paper

and a helper line under the field:

> Describe the border's style and 2–3 colors. The middle of the page
> always stays plain paper — that's where the words and pictures go.

## 4. Per-candidate regenerate (nice-to-have)

A small "↻ redo this one" on each card → same generate call with
`n_candidates: 1` (new draft row replaces the card; archive the old row).
Cheaper than regenerating all four when one is almost right.

## 5. Layout + flow polish

- Candidate grid: 2 columns, cards ≥ 280px wide (current 4-up thumbnails
  are too small to judge).
- "Publish selected" stays disabled until a card is selected; selected
  card gets a clear highlight ring.
- After publish, show the next-step nudge: "Published. Now attach it —
  set this theme on a relationship profile or campaign so videos use it."
  (And when the Theme dropdown lands on those editors, link straight to
  it.)
- The generic "Something went wrong — try again" on generate failures:
  use the error map from the earlier contract doc (`502` agent body →
  "The AI couldn't process this — try again"; `429` → "Rate limited —
  wait a minute"; `503` → "Image generation is not configured").

## 6. "Visual theme" dropdown on the campaign + profile editors (attach flow)

Today attaching a published theme means pasting its raw row id into the
ExtraFields JSON — unusable for a content person. Add a proper selector to
BOTH the campaign editor and the profile editor:

- A labeled select **"Visual theme"**: options = the visual_themes list
  filtered to `state === 'published'`, label `display_name (slug)`,
  **value = row `id`**; first option `(default — classic)` sending
  `visual_theme_id: null`/omitted.
- Bind it to the payload key `visual_theme_id` (move it out of
  ExtraFields into KNOWN_*_KEYS for both tables).
- Optional but great: a 60px thumbnail of the selected theme next to the
  select, via the existing authed image hook (404 → classic asset).
- Precedence hint under the select — campaigns: "Overrides the
  relationship profile's theme while this campaign applies." — profiles:
  "Used for every video of this relationship unless a campaign overrides
  it."
- Post-publish nudge on the Themes screen links here: "Published. Attach
  it: [campaign editor] [profile editor]".

**Ids are safe to hold now:** the agent repoints `visual_theme_id`
references automatically when a theme is edited/rolled back (supersession
mints a new id, references follow). So the dropdown can store the id it
saw at selection time without going stale.

## Acceptance

- [ ] Zones overlay renders on cards AND in the lightbox, toggleable.
- [ ] Lightbox at natural size with candidate paging.
- [ ] Brief placeholder + helper exactly as above.
- [ ] Publish disabled without selection; post-publish nudge shown.
- [ ] (If built) per-candidate regenerate creates one new draft card.

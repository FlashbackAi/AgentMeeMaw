# Node Prompt — Father's Day Storybook Cover (reference image + de-age)

**For:** the Node Backend team.
**Status:** agent side built (branch `feat/fathers-day-confession-storybook`);
Node work outstanding before the FD cover renders.

## What changed (agent side)

The Father's Day skin (`campaign = "fathers_day_2026"`) now produces a
**confession** storybook: first-person *about* the father (voice = "he"), with a
stylized **prime-years cover portrait** built from a contributor-uploaded photo.

The only piece the agent cannot execute is rendering the cover image from that
photo — that's Node's job. This doc is that contract.

## The new cover shape

For a storybook generation, the agent writes
`tributes.latest_generation_context.storybook.cover`. It may now carry **extra
fields** beyond the existing `caption` / `subtitle` / `style_preset` / `prompt` /
`negative`:

```jsonc
"cover": {
  "caption": "A man who spent himself so we'd never have to.", // defining phrase
  "subtitle": "",
  "style_preset": "storybook",
  "reference_s3_key": "uploads/<person>/prime.jpg", // NEW — present only for FD-with-photo
  "prompt": "a dignified painterly PORTRAIT of the subject in his prime years ...",
  "negative": "<COVER_PORTRAIT_NEGATIVE_PROMPT — no likeness ban>", // NEW relaxed negative
  "hero_line": "He could have owned the valley. He chose a report card." // NEW — optional
}
```

### `reference_s3_key` (new)
- An S3 key of the contributor-uploaded **prime/profile photo** of the subject.
- **When present:** render the cover **image-to-image** from that photo — a
  painterly prime-years portrait. The de-age instruction (when the uploaded
  photo is an older/current one) is already baked into `prompt` ("render him
  noticeably YOUNGER than the reference photo … keep his recognizable
  features").
- **When absent:** fall back to the **existing** cover behavior — if `prompt`
  is present, render it as the establishing-scene cover; otherwise reuse the
  first content still (today's default). No behavior change for non-FD tributes.

### `negative` (relaxed, cover only)
- For the reference-image cover, `negative` is `COVER_PORTRAIT_NEGATIVE_PROMPT`,
  which **drops** the `deepfake likeness of a real specific living person` /
  `visible faces of named subjects` bans.
- This is the **one sanctioned place** a real subject's likeness is rendered:
  the contributor uploaded their own consented photo of the subject and asked us
  to stylize it. Use `negative` verbatim from the context — do not re-add the
  likeness ban for the cover.
- **Page/scene art is unchanged.** Every entry in `pages[]` keeps the full
  `SCENE_NEGATIVE_PROMPT` (likeness ban intact). Only the cover relaxes it.

### `hero_line` (new, optional)
- A story-gated "fork in the road" line (e.g. *"He could have owned the valley.
  He chose a report card."*). Present **only** when the father's story revealed a
  concrete given-up path; absent otherwise.
- Render it as **secondary cover text** beneath the defining phrase (`caption`)
  when present; render nothing extra when absent.

## Where `prime_photo_s3_key` comes from — and the fallback chain

The cover is meant to be a **stylized portrait of the actual father** (brief
§2.3), so Node should **always send a photo key** for the FD storybook. Resolve
it as a fallback chain and send the first one that exists:

1. The **prime-years photo** uploaded in the Father's Day theme's photo question
   (a Node-owned S3 upload) — best case.
2. Else the subject's **existing profile / legacy photo** (`persons.image_url`
   source key) — the agent de-ages it to his prime years.

```
POST /tributes/{tribute_id}/generate
{
  "person_id": "<uuid>",
  "artifact_kind": "storybook",
  "campaign": "fathers_day_2026",
  "preset": "<optional>",
  "prime_photo_s3_key": "uploads/<person>/prime.jpg", // NEW — prime OR profile key
  "cover_photo_is_prime_years": true                  // NEW — see below
}
```

- Send the **prime-years key** when the contributor uploaded one, and set
  `cover_photo_is_prime_years: true` so the agent does **not** de-age it.
- Otherwise send the **profile/legacy photo key** and set (or leave the default)
  `cover_photo_is_prime_years: false` — the agent then de-ages an older/current
  photo to his prime years. Do **not** skip the field just because it isn't a
  dedicated prime photo.
- Only omit `prime_photo_s3_key` when the subject has **no photo at all** — then
  the cover falls back to the establishing-scene behavior (no portrait). This
  should be the rare case, not the default.

> `cover_photo_is_prime_years` matters: the agent can't tell a prime photo from a
> current one. If you send an actual prime-years photo but leave the flag
> `false`, the de-age instruction will make an already-young face look too young.
> Set it `true` for prime photos; `false` (default) for profile/current photos.

## Render checklist (per storybook job)

1. Read `storybooks` / `tributes.latest_generation_context.storybook` from
   Postgres (authoritative; the SQS message is trigger-only — unchanged).
2. For the **cover**: if `cover.reference_s3_key` is present, fetch that S3
   object and run image-to-image with `cover.prompt` + `cover.negative`;
   else render text-to-image from `cover.prompt` (or reuse the first still).
3. Bake `cover.caption` (defining phrase) and, when present, `cover.hero_line`
   onto the cover.
4. Render `pages[]` exactly as today (full scene negative, no likeness).
5. Upload, write URL columns, notify — all unchanged.

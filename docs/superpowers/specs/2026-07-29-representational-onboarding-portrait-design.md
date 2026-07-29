# Representational onboarding profile image (no-photo path)

**Date:** 2026-07-29
**Status:** approved (composition + scope + prototype look all user-confirmed)

## Problem

When a contributor finishes onboarding without uploading a photo, the
agent auto-generates a profile picture from the standard painterly
portrait prompt (`compose_image_prompt`). With no reference image, the
model invents a face for the name — the result reads as a portrait of a
random stranger presented as the subject. The contributor knows what
their person looks like; a confidently wrong face is worse than no
face.

## Decision

On the onboarding auto-generation path only, when no reference photo is
provided, generate a **representational illustration** instead of a
portrait: a single figure seen **from behind** — face never visible —
in the same design language as the tribute friendship-day banners
(`legacy/public/tribute/friendship-day-banner*.webp`): soft flat
storybook illustration, warm cream / terracotta / muted amber palette,
gentle hills and foliage, a few birds, generous negative space.

Prototype validated 2026-07-29 against `gemini-3.1-flash-image`
(aspect 1:1, prod key): four variants (female friend, grandfather,
mother, neutral friend) all landed on-style on the first attempt. User
approved the look as-is.

## Scope

| Surface | Behavior |
|---|---|
| Person creation (onboarding), **no** `reference_s3_key` | **NEW: representational prompt** (this design) |
| Person creation (onboarding), photo uploaded | unchanged — portrait prompt, `with_reference` |
| `POST /persons/{id}/profile-picture/generate` / `/edit` | unchanged — portrait prompt in both modes (user's explicit scope call; the invented-face portrait can still be produced via manual regenerate) |
| Artifact presets | not involved — onboarding path passes `preset=None` today and keeps doing so |
| Ground truth | not used — empty at person-creation time (onboarding answers land later). Consistent with invariant #26: no auto-regenerate when GT lands; manual regenerate is the recovery path |
| Node worker | zero changes — reads `latest_generation_context` exactly as today |
| Storybook / tribute likeness anchoring | unaffected — both gate on `mode == 'with_reference'`, which this image is not |

## Prompt composition

New function in `flashback/profile_picture/prompt.py`:

```
compose_representational_prompt(*, gender: str | None, relationship: str | None) -> str
```

The subject's **name is deliberately excluded** — with no face to
anchor, a name only imports risk (deity priors for names like Krishna,
ethnicity guessing). Two inputs shape the figure:

- **Figure noun** from `persons.gender` (`he` → "male figure", `she` →
  "female figure", `they`/`None`/other → "figure" plus an "androgynous
  silhouette" clause).
- **Relationship clause** from a small deterministic mapping on the
  lower-cased relationship string (word match, first hit wins):
  - grandmother / grandfather / grandparent → "an elderly {figure}'s
    gentle posture, simple everyday clothing"
  - mother / father / parent / aunt / uncle → "a {parent-word}'s warm
    unhurried posture in their middle years, simple everyday clothing"
  - friend / sibling / brother / sister / cousin / colleague →
    "dressed casually like a close {relationship}"
  - anything else / empty → "simple everyday clothing"

Base scene (validated by the prototype, kept verbatim in spirit):

> "Soft flat storybook illustration of a single {figure} seen from
> behind, {relationship clause}, walking along a gentle path into a
> warm landscape, cream and terracotta palette with muted amber
> accents, soft autumn foliage and rolling hills, a few birds high in
> the sky, generous negative space, clean minimal shapes, subtle paper
> grain, warm hopeful mood, the figure's face never visible, no text,
> no watermark"

New negative constant `REPRESENTATIONAL_NEGATIVE_PROMPT` (goes into the
generation context's `negative_prompt` field, same plumbing as the
portrait's `NEGATIVE_PROMPT`):

> visible face, frontal view, figure turning toward the viewer,
> portrait framing, close-up headshot, multiple people, crowd,
> photograph, photorealism, 3D render, plastic CGI look, harsh
> saturation, neon colors, religious deity iconography, halo, divine
> aura, multi-armed figure, text, watermark, signature

## Code changes

1. `flashback/profile_picture/prompt.py` — add
   `compose_representational_prompt` + `REPRESENTATIONAL_NEGATIVE_PROMPT`
   (export from `__init__`). Existing `compose_image_prompt` and
   `NEGATIVE_PROMPT` untouched.
2. `flashback/http/routes/persons.py::_create_once` — branch on
   `reference_s3_key`:
   - present → current behavior verbatim (portrait prompt +
     `NEGATIVE_PROMPT`, `mode="with_reference"`).
   - absent → representational prompt +
     `REPRESENTATIONAL_NEGATIVE_PROMPT`, `mode="no_reference"`.
   Context write → queue push flow stays identical (Postgres first,
   SQS trigger second; invariant in CLAUDE.md §3).

No migrations, no API shape changes, no Node work order.

## Testing

- Unit: `compose_representational_prompt` — gender variants (he / she /
  they / None), relationship-clause mapping (grandparent / parent /
  friend / unknown / empty), name never present, "seen from behind" and
  "face never visible" always present.
- Route: person-create without `reference_s3_key` writes a
  `latest_generation_context` whose prompt contains "seen from behind"
  and whose negative is `REPRESENTATIONAL_NEGATIVE_PROMPT`, with
  `mode="no_reference"`; with `reference_s3_key` the context carries the
  portrait prompt + `NEGATIVE_PROMPT` and `mode="with_reference"`
  (regression pin on the unchanged path).

## Out of scope (explicitly)

- Regenerate/edit endpoints switching styles when no photo is attached
  (user chose onboarding-only; revisit if the "rando person" complaint
  resurfaces via the regenerate button).
- Relationship-pair or symbolic-scene compositions (options considered
  and declined in favor of the single figure).
- Backfilling existing legacies' generated portraits — existing images
  stay until someone regenerates manually.

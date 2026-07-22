# Gender-correct, hallucination-clamped artifacts

**Date:** 2026-07-22
**Status:** design approved, spec under review
**Scope:** this repo (Python agent service). No Node changes. No DB migrations.

---

## 1. Problem

Families are complaining about the generated artifacts, storybooks worst of
all:

1. **Wrong genders.** A "friends" storybook where the contributor and the
   subject are both women renders a boy and a girl. Gender is captured on
   `persons` (`gender`, `contributor_gender` — migrations 0009/0031) but is
   **dropped on the way into the storybook assembler and the tribute
   assembler**, so both fall back to the image model's guess.
2. **Cover pages invent families.** The cover prompt asks for a scene
   "evocative of their life and world" and calls it "a family storybook",
   which the model reads as license to paint a whole invented family.
3. **Scene hallucinations.** The "draw ONLY the people the scene describes"
   rule only renders when the character roster is non-empty, so books whose
   roster comes back empty lose the anti-invention guard entirely. Recurring
   non-subject people also drift in gender/appearance panel to panel.
4. **Entity portraits** are painted with no gender signal for the entity
   itself (only subject + contributor are grounded today).

Root cause across all four: **gender that we already know never reaches the
prompt**, and the anti-hallucination rules are conditionally rendered or
missing on the cover.

## 2. Goals / non-goals

**Goals**
- Every artifact prompt that depicts a person states that person's gender
  **when we know it**, using one shared vocabulary.
- Gender is *known* from three sources: `persons.gender` (subject),
  `persons.contributor_gender` (contributor/storyteller), and — new — a
  captured `attributes.gender` on person-entities.
- Storybook covers show the **subject alone**; invented families become
  structurally impossible, not just discouraged.
- The "draw only who the scene names" rule fires unconditionally.
- Tribute video stops being gender-blind.

**Non-goals (YAGNI)**
- No per-character reference-image sheets for cast members (prompt-level
  consistency only, per the approved design).
- No new migration — entity gender rides the existing `entities.attributes`
  JSONB; context fields ride the existing `latest_generation_context` JSONB.
- No guessing gender from a name, ever (invariant #6: under-extract, drop).
- No change to likeness rules (faces still turned/distant on scenes; cover
  portrait likeness exception unchanged).

## 3. The shared vocabulary — `flashback/artifacts/people.py`

This module already maps a stored pronoun form (`he`/`she`/`they`/`None`) to
a figure noun (`a man`/`a woman`/neutral). We extend it, and it becomes the
single source of gender language for **all** surfaces.

Person-entity gender is stored as `attributes.gender ∈ {"male","female"}`
(the descriptor vocabulary the refs module already uses in `_AGE_DESCRIPTORS`),
distinct from the `he`/`she`/`they` pronoun form stored on `persons`.
`figure_noun` already lower-cases and maps; add `"male"→"a man"`,
`"female"→"a woman"` so both vocabularies resolve through one function.
`they`/`None`/unknown continue to yield no directive (never a wrong guess).

New helper `people_catalog_fragment(...)` renders a `<people>` grounding block
from: subject (name + relationship + gender), contributor (gender + "the
person sharing these memories"), and a list of involved person-entities
(`name`, `relationship`, `gender`). Only entries with a known gender emit a
gender clause; the rest still list name/role so the model knows who exists but
stays unbiased on presentation. Returns `""` when nothing is known.

The existing `people_scene_fragment` (subject + contributor) stays for the
moment/scene artifact compose path and is reimplemented on top of the shared
noun map.

## 4. Capture entity gender at extraction

**Prompt** (`workers/extraction/prompts.py`): add to the person-entity rules —

> For person entities, populate `attributes.gender` (`"male"` or `"female"`)
> **only** when the conversation makes it unambiguous (an explicit pronoun,
> a gendered relationship word like "my sister" / "his uncle", or a direct
> statement). NEVER infer gender from a first name alone — if it is not clear
> from what was said, omit the field.

`attributes` is already a free-form `{"type":"object"}` in the tool schema, so
no schema change is required.

**Deterministic reuse** (`persistence._persist_entities` /
`insert_entities_async`, invariant #17a): when an extracted entity resolves to
an existing active same-kind row, fold a newly-known `gender` into the stored
attributes **only when the stored value is empty** — mirroring exactly how the
path already fills an empty description and folds aliases. Never overwrite an
existing gender (a later ambiguous mention must not clobber a confident one).

## 5. Storybook script assembly (the friend-book root fix)

**Route → context.** `fetch_person_for_storybook_async` already selects
`contributor_gender`; today `_context` passes only `gender`. Extend:
- `StorybookRenderContext` / `build_context_dict` gain `contributor_gender:
  str | None` and `people: list[dict]` (each `{name, relationship, gender}`).
  Both default to None/`[]` so pre-existing stored contexts deserialize
  unchanged.
- `_fetch_inputs` additionally loads the **active, person-kind entities
  involved** in the selected moments (via `involves` edges) with their
  `attributes.gender` and `attributes.relationship`. Scoped by `person_id`.
  This is the cast the book will actually draw.
- `_context` passes `contributor_gender` and `people` through.

**Assembler** (`storybook/script.py`):
- User message gains a `<people>` block rendered from the shared helper:
  the storyteller's gender + role, and each involved person-entity's
  name/role/gender.
- `characters` roster schema gains a **required** `gender` field
  (`enum: ["male","female","unknown"]`). The `Character` dataclass +
  `to_dict`/`from_dict` carry it; stored scripts predating the field
  deserialize with `gender="unknown"`.
- CAST rule: "Set each character's `gender` from `<people>` or from an
  explicit pronoun in the memories; use `"unknown"` only when neither says.
  Never guess gender from a name."
- The AGES rule becomes **AGES & GENDERS**: every scene line must state the
  apparent age **and** gender of every person present ("his friend, a woman
  of about sixty"), with the same rationale already given for age — panels are
  illustrated independently, so an unstated attribute WILL be drawn wrong.
- The subject's own gender is stated once in the `<subject>` framing so the
  throughline figure is never mis-presented.

## 6. Storybook image prompts (`storybook/refs.py`, `storybook/scenes.py`)

- `cast_rule` renders each character's gender inline:
  `"Aarav (his friend, a man): <appearance>"`. The mapping goes through the
  shared noun map; `unknown` omits the noun.
- The **"draw ONLY the people the scene describes — do not add anyone it does
  not mention, and NEVER show the same face twice"** clause moves out of
  `cast_rule` (which only renders with a non-empty roster) into the
  unconditional tail of `_ident(...)` so it is present on **every** scene and
  chapter panel, roster or not.
- `identity_rule` keeps its existing subject-only binding; no change needed
  beyond the unconditional clause above.

**Cover rewrite** (`gen_cover_art`): the cover shows the **subject alone**.
- Drop "a family storybook" framing and "evocative of their life and world".
- New prompt: "A single-figure cover portrait-scene of {name} ({rel}),
  {gender-noun}, {age}. {name} is the ONLY person in the image — no other
  people, no family, no crowd, no bystanders. Evoke their world through
  place, light, objects and atmosphere ONLY, never through other people."
- Keep the dominant-age-stage ref anchor (`cover_stage`) and the likeness
  binding to the subject.
- `age_descriptor` already takes gender; the cover already passes it. The
  gender noun is added to the sentence explicitly for redundancy with the
  ref image.

## 7. Tribute video (`tribute_video/*`)

- `RenderContext` / `build_context_dict` gain `gender` + `contributor_gender`
  (default None → byte-identical behavior for snapshots written before this
  change; manual regenerate re-resolves fresh per the snapshot rule in §3 of
  CLAUDE.md).
- The route that composes the tribute context reads both from `persons` and
  writes them into the context.
- Assembler (`tribute_video/assembler.py`): `<subject>` carries the subject's
  gender; the art-direction guidance instructs gendered nouns for the subject
  and storyteller figures. The hardcoded `relationship or "grandfather"`
  fallback becomes neutral (`relationship or "the subject"`), so a missing
  relationship no longer forces a male grandfather.
- `page_render/art.py` `portrait_from_photo`: the hardcoded `his` /
  "restore his prime-years self" becomes pronoun-parameterized from the
  subject gender (his/her/their + "fuller dark hair" stays as generic
  prime-years vigour language that reads for any gender). `deage` clause
  gender-neutralized.

## 8. Entity portraits

- **Extraction prompt**: an entity's `generation_prompt` must state the
  figure's gender presentation when `attributes.gender` is known, using the
  same matching-noun language as the scene rule.
- **Regenerate/edit** (`http/routes/artifacts.py` → `compose_scene_prompt`):
  today `people_scene_fragment` grounds subject + contributor. For a
  `record_type=entity` regenerate, also append the **entity's own** stored
  gender so a portrait of Aarav is grounded as "a man" even though Aarav is
  neither the subject nor the contributor. Threads (abstract arcs) and the
  person portrait flow are unchanged.

## 9. Testing

Unit tests, per surface, no live image calls:
- `people.py`: noun map covers `male`/`female`/`he`/`she`/`they`/None/junk;
  `people_catalog_fragment` renders/omits clauses correctly.
- Extraction: entity with a clear pronoun gets `attributes.gender`; entity
  named only by a first name does **not**; deterministic-reuse fills an empty
  gender and never overwrites a set one.
- Storybook context: round-trips with and without the new fields; old stored
  dict (no `contributor_gender`/`people`) deserializes to defaults.
- Script: roster `from_dict` defaults missing `gender` to `"unknown"`;
  `<people>` block appears in the user message; AGES & GENDERS rule text
  present.
- Image prompts: `cast_rule` includes the gender noun; the "draw only who the
  scene names" clause is present with an empty roster; cover prompt names the
  subject as the only figure and omits family framing.
- Tribute: context round-trips with new fields defaulting None; assembler
  `<subject>` carries gender; `portrait_from_photo` uses the right pronoun for
  each gender; neutral relationship fallback.

Manual verification via the `verify` skill: mint a same-gender friends
storybook on the dev stack and confirm cover = subject alone, cast genders
correct. Existing books/tributes re-render correctly through regenerate
(contexts re-resolve fresh).

## 10. Risk / rollback

- Pure prompt + JSONB-plumbing change; no schema, no Node contract change.
- Old stored contexts deserialize to safe defaults (behavior unchanged until
  a regenerate re-resolves), so there is no backfill and no forward-compat
  break.
- Roster `gender` is required in the tool schema but defaulted on read, so a
  model that ever omits it (or an old stored script) never crashes assembly.
- Rollback is a straight revert; nothing persisted changes shape
  destructively (new attribute keys are additive).

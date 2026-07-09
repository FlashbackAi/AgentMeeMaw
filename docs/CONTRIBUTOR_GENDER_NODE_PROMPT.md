# Node Prompt — Contributor gender at onboarding

**For:** the Node Backend team.
**Status:** agent side built + merged to `main` (commits `6338561`…`257869a`);
one small Node onboarding change outstanding. Requires agent migration **0031**
applied to Postgres before the new field accepts values.

## Why

Some generated moment artifacts came out with the wrong gender — the image
model defaulted figures to female. Two causes, both now fixed agent-side:

1. The **subject's** gender (`persons.gender`) existed but never reached moment
   generation.
2. The **contributor** — who is depicted alongside the subject in some memories
   ("my father and I on a bike") — had **no gender field at all**.

The agent now threads both genders into every art prompt (moments, regenerate,
tributes, storybook). The subject's gender you already collect and send. The
**only new thing Node must do is collect and send the contributor's gender.**

## The contract change — `POST /persons`

One new optional field, symmetric with the existing `gender`:

```jsonc
{
  "name": "string",                       // subject display name
  "relationship": "string",               // contributor's relationship to subject
  "contributor_display_name": "string",
  "gender": "he | she | they | null",     // SUBJECT's pronoun — already sent today
  "contributor_gender": "he | she | they | null",  // NEW — the CONTRIBUTOR's pronoun
  "reference_s3_key": "string (optional)"
}
```

Response now echoes it back too:

```jsonc
{
  "person_id": "uuid",
  "name": "string",
  "relationship": "string",
  "gender": "he | she | they | null",
  "contributor_gender": "he | she | they | null",  // NEW
  "phase": "starter",
  "created_at": "iso-8601"
}
```

- `gender` = the **subject's** pronoun (the person the legacy is about).
- `contributor_gender` = the **contributor's** pronoun (the person talking).
- Values are pronoun form: `"he"`, `"she"`, `"they"`, or omitted/`null`.
- `they` / omitted leaves that figure **gender-neutral** in art — the agent
  never guesses, so a missing value is safe but loses the fix for that person.

## What Node must do

1. **Onboarding UX:** add a contributor-gender question alongside the existing
   subject-gender question. Same three options (he / she / they), optional. The
   wording should make clear it is about *the person creating the legacy*, not
   the subject — e.g. "And you — how should we picture you?" vs the subject's
   "How should we picture {name}?".
2. **Send it:** include `contributor_gender` in the `POST /persons` body. That
   is the entire write path — single-contributor v1, so it lives on the
   agent-owned `persons` row (CLAUDE.md §3). There is no separate endpoint.
3. **(Optional) Surface it on edit:** if the onboarding-review screen lets the
   contributor correct the subject's gender, mirror the same control for
   `contributor_gender`. Re-creating the person is not required; if you need a
   post-onboarding edit path for it, ask the agent team to expose one (none
   exists today — same as subject `gender`).

## What Node does NOT need to change

- **Image / video generation calls are unchanged.** The gender instructions are
  baked into the prompt **text** the agent writes to
  `<table>.latest_generation_context.prompt` (and per-scene `prompt` fields in
  tribute/storybook contexts). Node keeps reading the prompt from Postgres and
  passing it to the model exactly as today — no new fields to parse, no negative
  prompt changes.
- **Reduced photorealism** ships the same way: the agent nudged the painterly /
  oil-illustration wording inside the prompt text. Node renders the prompt
  verbatim — nothing to do.
- **No schema reads.** `persons.contributor_gender` is agent-owned; Node never
  reads or writes the canonical graph for this. It travels only on the
  `POST /persons` request/response.

## Acceptance check

1. Create a legacy where subject = a man, contributor = a woman; send
   `gender: "he"`, `contributor_gender: "she"`.
2. Have a session that produces a moment featuring both ("my father and I …").
3. When the moment artifact renders, the subject should read male and the
   contributor female — no female-default on the subject.
4. A legacy created with both omitted should behave exactly as before (neutral
   figures) — confirms backward compatibility for existing/legacy clients.

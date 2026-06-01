-- Migration 0023: latest_generation_context JSONB on artifact-bearing rows.
--
-- Codifies the Postgres-authoritative artifact-generation model: the agent
-- composes the full generation context (prompt + negative + mode + reference
-- key + preset) on every auto / regenerate / edit call and writes it to this
-- column BEFORE pushing the (minimal) SQS message. Node's worker reads this
-- column at processing time — the SQS message is a trigger, not a payload.
--
-- Coexists with the existing `generation_prompt` column, which keeps its
-- prior meaning: the LLM-emitted BASE scene description, immutable through
-- edits. The agent re-composes from base + Node-supplied prior_instructions
-- on each edit and stores the composed result here. Two columns, two roles:
--   generation_prompt        — immutable base (LLM-emitted at creation)
--   latest_generation_context — mutable composition (latest authored job)
--
-- Shape of the JSONB:
--   {
--     "prompt":            "<composed prompt — pass to model>",
--     "negative_prompt":   "<negative — pass to model>" | null,
--     "mode":              "no_reference" | "with_reference",
--     "reference_s3_key":  "<s3 key>" | null,
--     "preset":            "<slug>" | null,
--     "source":            "auto" | "regenerate" | "edit",
--     "composed_at":       "<ISO-8601 UTC>"
--   }

BEGIN;

ALTER TABLE persons   ADD COLUMN latest_generation_context JSONB;
ALTER TABLE moments   ADD COLUMN latest_generation_context JSONB;
ALTER TABLE entities  ADD COLUMN latest_generation_context JSONB;
ALTER TABLE threads   ADD COLUMN latest_generation_context JSONB;

-- Backfill: for existing rows that already have a generation_prompt, seed
-- a minimal context so any in-flight or replayed SQS messages have something
-- to read. We use NULL negative / no_reference / null preset / 'auto' source
-- — the agent's defaults at creation time.
UPDATE persons
   SET latest_generation_context = jsonb_build_object(
         'prompt',           generation_prompt,
         'negative_prompt',  NULL,
         'mode',             'no_reference',
         'reference_s3_key', NULL,
         'preset',           NULL,
         'source',           'auto',
         'composed_at',      to_char(now() AT TIME ZONE 'UTC',
                                     'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
       )
 WHERE generation_prompt IS NOT NULL
   AND latest_generation_context IS NULL;

UPDATE moments
   SET latest_generation_context = jsonb_build_object(
         'prompt',           generation_prompt,
         'negative_prompt',  NULL,
         'mode',             'no_reference',
         'reference_s3_key', NULL,
         'preset',           NULL,
         'source',           'auto',
         'composed_at',      to_char(now() AT TIME ZONE 'UTC',
                                     'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
       )
 WHERE generation_prompt IS NOT NULL
   AND latest_generation_context IS NULL;

UPDATE entities
   SET latest_generation_context = jsonb_build_object(
         'prompt',           generation_prompt,
         'negative_prompt',  NULL,
         'mode',             'no_reference',
         'reference_s3_key', NULL,
         'preset',           NULL,
         'source',           'auto',
         'composed_at',      to_char(now() AT TIME ZONE 'UTC',
                                     'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
       )
 WHERE generation_prompt IS NOT NULL
   AND latest_generation_context IS NULL;

UPDATE threads
   SET latest_generation_context = jsonb_build_object(
         'prompt',           generation_prompt,
         'negative_prompt',  NULL,
         'mode',             'no_reference',
         'reference_s3_key', NULL,
         'preset',           NULL,
         'source',           'auto',
         'composed_at',      to_char(now() AT TIME ZONE 'UTC',
                                     'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
       )
 WHERE generation_prompt IS NOT NULL
   AND latest_generation_context IS NULL;

COMMIT;

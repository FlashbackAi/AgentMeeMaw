-- Ground-truth layer (design 2026-06-11): machine-consumable stable
-- subject facts. One key per registry field; each value is
--   {"value": ..., "provenance": "onboarding|inferred|tap|user_edit",
--    "confidence": "low|medium|high", "updated_at": "<ISO-8601>"}
-- The field registry lives in code (flashback/ground_truth/registry.py).
ALTER TABLE persons
    ADD COLUMN IF NOT EXISTS ground_truth JSONB NOT NULL DEFAULT '{}'::jsonb;

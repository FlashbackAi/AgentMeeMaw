-- ============================================================================
-- cleanup_test_legacies.sql
-- Deletes test legacies (persons + entire graph) where profile_summary IS
-- NULL — i.e. legacies that never had a session wrapped.
--
-- Usage:
--   1. Run the inventory query below (SELECT only) to preview what matches
--      the criterion (profile_summary IS NULL = never had a session wrapped).
--   2. Run the DELETE block. It is one transaction: review the row-count
--      output, then it commits atomically.
--
-- CAUTION: a real legacy that was just created but never wrapped a session
-- also has profile_summary NULL. Check the inventory output before deleting.
--
-- What cascades automatically from DELETE FROM persons:
--   moments (+ moment_history), entities, threads, traits, questions
--   (person-scoped only; global starter_anchor templates have person_id NULL
--   and are untouched), themes, profile_facts, tributes, storybooks,
--   identity_merge_suggestions, extraction_outbox, processed_extractions,
--   processed_trait_syntheses, processed_profile_summaries,
--   processed_producer_runs.
--
-- What this script handles explicitly:
--   edges              — no FKs, cascade never touches it
--   question_decisions — FK to persons/questions WITHOUT cascade (would
--                        otherwise abort the persons delete)
--
-- What is intentionally left alone:
--   usage_events — cost ledger, no FK, keep for accounting
--   Valkey       — session keys expire via TTL (24h)
--   S3 / Dynamo  — Node-owned; images and transcripts for deleted legacies
--                  become orphans (clean from the Node side if needed)
-- ============================================================================


-- ----------------------------------------------------------------------------
-- STEP 1 — INVENTORY (read-only). Preview exactly what STEP 2 will delete.
-- ----------------------------------------------------------------------------

SELECT p.id,
       p.name,
       p.relationship,
       p.phase,
       p.created_at::date AS created,
       (SELECT count(*) FROM moments    m WHERE m.person_id = p.id AND m.status = 'active') AS moments,
       (SELECT count(*) FROM entities   e WHERE e.person_id = p.id AND e.status = 'active') AS entities,
       (SELECT count(*) FROM tributes   t WHERE t.person_id = p.id)                          AS tributes,
       (SELECT count(*) FROM storybooks s WHERE s.person_id = p.id)                          AS storybooks
FROM persons p
WHERE p.profile_summary IS NULL
ORDER BY p.created_at;


-- ----------------------------------------------------------------------------
-- STEP 2 — DELETE every person matching the criterion, then run this block.
-- ----------------------------------------------------------------------------

BEGIN;

CREATE TEMP TABLE doomed_persons (id uuid PRIMARY KEY) ON COMMIT DROP;

INSERT INTO doomed_persons (id) VALUES
    ('15a94b6a-4cc0-4741-b3f0-2f26846e9512'),
    ('3367c77b-d131-4a29-8c5a-4ca078cf4b44'),
    ('870c7bb2-6b56-4dd8-9fd7-cc473b7a07bb'),
    ('fe8ac58d-8db7-4b5d-accc-2558a473f06c'),
    ('abe35c0a-2ba8-44af-a50d-f7a00493057a'),
    ('bd378a1c-0a05-462b-a616-c0d3ff81744a'),
    ('461723d4-7cfa-4504-ba0c-9c3624b63377'),
    ('a6dfbbb2-2a3e-4af0-89a8-973fa124c0ba'),
    ('e90bfb93-c6a5-4b27-883d-c7b539a7a625'),
    ('2a90c3e1-48bc-4553-9dbf-4ebe581f550c'),
    ('5a2b0bb8-bbad-49ff-a911-49672f830d3c'),
    ('84b02d8c-e9d8-40d1-afe3-fbce6fdf66f1'),
    ('81e22117-b2c3-4415-9f81-daa2dd296210'),
    ('83eb34a2-539e-47b8-82f8-481ba7f1a52e'),
    ('df4b9f46-9624-4e1f-98bc-5dc98d8d37b5'),
    ('6383f8e5-325e-4edc-a337-38d785611545'),
    ('0a028819-6bde-4276-b635-65346ff433a5'),
    ('1b3a6fa6-a6fe-44b4-89f7-5de82a016eea'),
    ('f65fea3d-e6e3-4d70-bd81-7cbd1a786e92'),
    ('c72049b3-8490-4b71-ba03-8afde8195fd1'),
    ('cfb822ec-1e79-4ef8-8cad-58c292df72a7'),
    ('1e3385ef-226e-4001-8453-182aea511a23'),
    ('a6a1ad90-4b95-40e7-acde-58541332c5bb'),
    ('050e4774-8526-43ca-82eb-22cc41c31607'),
    ('507f1b95-6a0f-4b41-af2c-c92c13c249c3'),
    ('0c244066-633d-497c-95ba-e27a6b65ace9'),
    ('3175288c-1c58-4d52-9473-a74c360a0ebd'),
    ('da1935da-4443-4b43-9ddf-b5ec606b6e5b');

-- Sanity check: names of what is about to be deleted.
SELECT p.id, p.name FROM persons p JOIN doomed_persons d ON p.id = d.id;

-- 2a. edges — delete every edge touching any node owned by these persons.
WITH doomed_nodes AS (
    SELECT id FROM doomed_persons
    UNION ALL SELECT m.id FROM moments   m JOIN doomed_persons d ON m.person_id = d.id
    UNION ALL SELECT e.id FROM entities  e JOIN doomed_persons d ON e.person_id = d.id
    UNION ALL SELECT t.id FROM threads   t JOIN doomed_persons d ON t.person_id = d.id
    UNION ALL SELECT t.id FROM traits    t JOIN doomed_persons d ON t.person_id = d.id
    UNION ALL SELECT q.id FROM questions q JOIN doomed_persons d ON q.person_id = d.id
)
DELETE FROM edges
WHERE from_id IN (SELECT id FROM doomed_nodes)
   OR to_id   IN (SELECT id FROM doomed_nodes);

-- 2b. question_decisions — no cascade on its FKs; clear by person AND by
--     question so the questions cascade can't be blocked.
DELETE FROM question_decisions
WHERE person_id  IN (SELECT id FROM doomed_persons)
   OR question_id IN (SELECT q.id FROM questions q
                      JOIN doomed_persons d ON q.person_id = d.id);

-- 2c. persons — everything else cascades from here.
DELETE FROM persons
WHERE id IN (SELECT id FROM doomed_persons);

COMMIT;

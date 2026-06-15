-- ============================================================================
-- 0027_tributes.up.sql
-- Flashback AI: Legacy Mode  -  Tribute output layer
-- ----------------------------------------------------------------------------
-- Tribute output (design 2026-06-14): a contributor-voiced shareable tribute
-- video + a general storybook. One row per tribute output per person (NOT
-- 1:1 -- a contributor may make more than one over time).
--
-- Adds:
--   * 'tribute' allowed as a themes.kind (alongside universal/emergent).
--     Tributes carry no originating thread (like universals).
--   * tributes table
--   * tribute_status view -- the Node read surface. Computes the four
--     completion-checklist slots and a weighted percent from the existing
--     graph + the tribute row's message_text. WEIGHTS LIVE HERE ONLY so the
--     agent (steering, live meter) and Node never drift.
--
-- Slot definitions (mirrored as display copy in flashback/tribute/checklist.py):
--   memories   (weight 40) = >= 3 qualifying active moments for the person
--   message    (weight 30) = tribute.message_text present
--   appearance (weight 20) = ground_truth has region + (birth_era|era_span)
--                            + one of distinctive_features|attire|build
--   signature  (weight 10) = >= 1 active trait OR an active entity carrying a
--                            'saying'/'mannerism' attribute
-- 'Qualifying' moment = active AND has any of: sensory_details, time_anchor,
-- an involves edge to any entity.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- themes.kind: allow 'tribute'
-- ----------------------------------------------------------------------------
-- The 0020 inline CHECK is auto-named themes_kind_check; the thread rule is
-- the named chk_themes_kind_thread. Rebuild both to admit 'tribute'.

ALTER TABLE themes DROP CONSTRAINT IF EXISTS themes_kind_check;
ALTER TABLE themes
    ADD CONSTRAINT themes_kind_check
    CHECK (kind IN ('universal', 'emergent', 'tribute'));

ALTER TABLE themes DROP CONSTRAINT IF EXISTS chk_themes_kind_thread;
ALTER TABLE themes
    ADD CONSTRAINT chk_themes_kind_thread CHECK (
        (kind = 'emergent' AND thread_id IS NOT NULL)
        OR
        (kind IN ('universal', 'tribute') AND thread_id IS NULL)
    );

-- ----------------------------------------------------------------------------
-- tributes table
-- ----------------------------------------------------------------------------

CREATE TABLE tributes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    theme_id  UUID REFERENCES themes(id) ON DELETE SET NULL,

    message_text         TEXT,    -- polished contributor message (Plan 2)
    message_source_turns JSONB,   -- raw words it was distilled from

    script           JSONB,       -- assembled scenes/captions (Plan 3)
    scene_moment_ids  UUID[],      -- which moments became scenes (Plan 3)
    checklist_state  JSONB,        -- snapshot at assembly time (Plan 3)

    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'ready', 'generating', 'complete', 'superseded')),

    -- Node writes URLs; we only ever write prompts/context (CLAUDE.md section 3).
    video_url     TEXT,
    image_url     TEXT,
    thumbnail_url TEXT,
    generation_prompt         TEXT,
    latest_generation_context JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX tributes_person_idx ON tributes (person_id, status);
CREATE INDEX tributes_theme_idx  ON tributes (theme_id) WHERE theme_id IS NOT NULL;

CREATE TRIGGER trg_tributes_updated_at BEFORE UPDATE ON tributes
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- ----------------------------------------------------------------------------
-- tribute_status view (Node read surface)
-- ----------------------------------------------------------------------------

CREATE VIEW tribute_status AS
SELECT
    tr.id,
    tr.person_id,
    tr.theme_id,
    tr.status,
    COALESCE(mem.qualifying_count, 0)                       AS memories_count,
    (tr.message_text IS NOT NULL
        AND length(btrim(tr.message_text)) > 0)             AS message_present,
    COALESCE(appr.appearance_present, false)                AS appearance_present,
    COALESCE(sig.signature_present, false)                  AS signature_present,
    (
        (LEAST(COALESCE(mem.qualifying_count, 0), 3)::numeric / 3 * 40)
      + (CASE WHEN tr.message_text IS NOT NULL
                AND length(btrim(tr.message_text)) > 0 THEN 30 ELSE 0 END)
      + (CASE WHEN COALESCE(appr.appearance_present, false) THEN 20 ELSE 0 END)
      + (CASE WHEN COALESCE(sig.signature_present, false) THEN 10 ELSE 0 END)
    )::int                                                  AS percent,
    (
        COALESCE(mem.qualifying_count, 0) >= 3
        AND tr.message_text IS NOT NULL
        AND length(btrim(tr.message_text)) > 0
        AND COALESCE(appr.appearance_present, false)
        AND COALESCE(sig.signature_present, false)
    )                                                       AS ready,
    tr.video_url,
    tr.image_url,
    tr.thumbnail_url,
    tr.created_at,
    tr.updated_at
FROM tributes tr
LEFT JOIN LATERAL (
    SELECT COUNT(*) AS qualifying_count
      FROM active_moments m
     WHERE m.person_id = tr.person_id
       AND (
            m.sensory_details IS NOT NULL
         OR m.time_anchor IS NOT NULL
         OR EXISTS (
             SELECT 1 FROM edges ie
              WHERE ie.from_kind = 'moment'
                AND ie.from_id   = m.id
                AND ie.edge_type = 'involves'
                AND ie.status    = 'active'
         )
       )
) mem ON true
LEFT JOIN LATERAL (
    SELECT (
        (p.ground_truth -> 'region' ->> 'value') IS NOT NULL
        AND (
            (p.ground_truth -> 'birth_era' ->> 'value') IS NOT NULL
            OR (p.ground_truth -> 'era_span' ->> 'value') IS NOT NULL
        )
        AND (
            (p.ground_truth -> 'distinctive_features' ->> 'value') IS NOT NULL
            OR (p.ground_truth -> 'attire' ->> 'value') IS NOT NULL
            OR (p.ground_truth -> 'build' ->> 'value') IS NOT NULL
        )
    ) AS appearance_present
      FROM persons p
     WHERE p.id = tr.person_id
) appr ON true
LEFT JOIN LATERAL (
    SELECT (
        EXISTS (
            SELECT 1 FROM active_traits tt WHERE tt.person_id = tr.person_id
        )
        OR EXISTS (
            SELECT 1 FROM active_entities en
             WHERE en.person_id = tr.person_id
               AND (en.attributes ? 'saying' OR en.attributes ? 'mannerism')
        )
    ) AS signature_present
) sig ON true
WHERE tr.status <> 'superseded';

COMMIT;

-- ============================================================================
-- 0020_themes.up.sql
-- Flashback AI: Legacy Mode  -  Themes layer
-- ----------------------------------------------------------------------------
-- Adds:
--   * themes table         (universal + emergent thematic groupings)
--   * 'theme' allowed as edge from_kind/to_kind
--   * 'themed_as' edge type (moment -> theme)
--   * active_themes view
--   * active_themes_with_tier view (denormalized read surface for Node)
--
-- Backfills 5 universal themes for every existing person:
--   family, career, friendships, beliefs, milestones
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- themes table
-- ----------------------------------------------------------------------------

CREATE TABLE themes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,

    kind TEXT NOT NULL CHECK (kind IN ('universal', 'emergent')),
    slug TEXT NOT NULL,                              -- 'family', 'cricket'
    display_name TEXT NOT NULL,                      -- 'Family', 'Love of cricket'
    description TEXT,                                -- LLM-generated; primarily emergent

    state TEXT NOT NULL DEFAULT 'locked'
        CHECK (state IN ('locked', 'unlocked')),

    archetype_questions JSONB,                       -- cached MC payload, NULL until generated
    archetype_answers JSONB,                         -- user's selections, NULL until unlocked
    unlocked_at TIMESTAMPTZ,

    -- For emergents: the originating thread (NULL for universals).
    thread_id UUID REFERENCES threads(id) ON DELETE SET NULL,

    -- Stylized artifact (image). Node writes URL, we write the prompt.
    image_url TEXT,
    thumbnail_url TEXT,
    generation_prompt TEXT,

    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded')),

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- emergents must reference a thread; universals must not
    CONSTRAINT chk_themes_kind_thread CHECK (
        (kind = 'universal' AND thread_id IS NULL)
        OR
        (kind = 'emergent'  AND thread_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX themes_active_slug_unique
    ON themes (person_id, slug)
    WHERE status = 'active';
CREATE INDEX themes_person_state_idx
    ON themes (person_id, state, status);
CREATE INDEX themes_thread_id_idx
    ON themes (thread_id)
    WHERE thread_id IS NOT NULL AND status = 'active';

CREATE TRIGGER trg_themes_updated_at BEFORE UPDATE ON themes
    FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- ----------------------------------------------------------------------------
-- edges: extend CHECK constraints
-- ----------------------------------------------------------------------------
-- Themes participate in edges as `to` (moment -> theme via themed_as).
-- We don't currently emit edges FROM themes, but symmetry in the kind enum
-- keeps validate_edge() honest and forward-compatible.
-- ----------------------------------------------------------------------------

ALTER TABLE edges DROP CONSTRAINT IF EXISTS edges_from_kind_check;
ALTER TABLE edges DROP CONSTRAINT IF EXISTS edges_to_kind_check;
ALTER TABLE edges DROP CONSTRAINT IF EXISTS edges_edge_type_check;

ALTER TABLE edges
    ADD CONSTRAINT edges_from_kind_check
    CHECK (from_kind IN
        ('moment', 'entity', 'thread', 'trait', 'question', 'person', 'theme'));
ALTER TABLE edges
    ADD CONSTRAINT edges_to_kind_check
    CHECK (to_kind IN
        ('moment', 'entity', 'thread', 'trait', 'question', 'person', 'theme'));
ALTER TABLE edges
    ADD CONSTRAINT edges_edge_type_check
    CHECK (edge_type IN (
        'involves',
        'happened_at',
        'exemplifies',
        'evidences',
        'related_to',
        'motivated_by',
        'targets',
        'answered_by',
        'themed_as'
    ));

-- ----------------------------------------------------------------------------
-- active_themes view
-- ----------------------------------------------------------------------------

CREATE VIEW active_themes AS
    SELECT * FROM themes WHERE status = 'active';

-- ----------------------------------------------------------------------------
-- active_themes_with_tier view
-- ----------------------------------------------------------------------------
-- Denormalized read surface for Node. Computes per-theme stats from
-- themed_as edges over active moments and derives the tier.
--
-- Tier rules:
--   tale       = qualifying_count >= 1
--   story      = qualifying_count >= 3 OR life_period_count >= 2
--   testament  = qualifying_count >= 5 AND life_period_count >= 3
--                AND has_rich_sensory
--
-- 'Qualifying' moment = active AND has any of:
--   sensory_details, time_anchor, an involves edge to any entity.
-- 'Rich sensory' = sensory_details length > 80 characters.
--
-- Locked themes always report tier = NULL.
-- ----------------------------------------------------------------------------

CREATE VIEW active_themes_with_tier AS
SELECT
    t.id,
    t.person_id,
    t.kind,
    t.slug,
    t.display_name,
    t.description,
    t.state,
    (t.archetype_questions IS NOT NULL) AS archetype_ready,
    t.unlocked_at,
    t.thread_id,
    t.image_url,
    t.thumbnail_url,
    t.created_at,
    t.updated_at,
    COALESCE(stats.qualifying_count, 0)   AS qualifying_count,
    COALESCE(stats.life_period_count, 0)  AS life_period_count,
    COALESCE(stats.has_rich_sensory, false) AS has_rich_sensory,
    CASE
        WHEN t.state = 'locked' THEN NULL
        WHEN COALESCE(stats.qualifying_count, 0) >= 5
         AND COALESCE(stats.life_period_count, 0) >= 3
         AND COALESCE(stats.has_rich_sensory, false) THEN 'testament'
        WHEN COALESCE(stats.qualifying_count, 0) >= 3
          OR COALESCE(stats.life_period_count, 0) >= 2 THEN 'story'
        WHEN COALESCE(stats.qualifying_count, 0) >= 1 THEN 'tale'
        ELSE NULL
    END AS tier
FROM themes t
LEFT JOIN LATERAL (
    SELECT
        COUNT(*) FILTER (
            WHERE m.sensory_details IS NOT NULL
               OR m.time_anchor IS NOT NULL
               OR EXISTS (
                   SELECT 1 FROM edges ie
                    WHERE ie.from_kind = 'moment'
                      AND ie.from_id = m.id
                      AND ie.edge_type = 'involves'
                      AND ie.status = 'active'
               )
        ) AS qualifying_count,
        COUNT(DISTINCT m.life_period_estimate) FILTER (
            WHERE m.life_period_estimate IS NOT NULL
              AND m.life_period_estimate <> ''
        ) AS life_period_count,
        bool_or(
            m.sensory_details IS NOT NULL
            AND char_length(m.sensory_details) > 80
        ) AS has_rich_sensory
      FROM edges e
      JOIN active_moments m ON m.id = e.from_id
     WHERE e.from_kind = 'moment'
       AND e.to_kind   = 'theme'
       AND e.to_id     = t.id
       AND e.edge_type = 'themed_as'
       AND e.status    = 'active'
       AND m.person_id = t.person_id
) stats ON true
WHERE t.status = 'active';

-- ----------------------------------------------------------------------------
-- Backfill: seed 5 universal themes for every existing person
-- ----------------------------------------------------------------------------
-- Idempotent via the active-slug unique index + ON CONFLICT DO NOTHING.
-- Future persons get seeded by application code in onboarding persistence.
-- ----------------------------------------------------------------------------

INSERT INTO themes (person_id, kind, slug, display_name, state)
SELECT p.id, 'universal', u.slug, u.display_name, 'locked'
  FROM persons p
  CROSS JOIN (VALUES
      ('family',      'Family'),
      ('career',      'Career'),
      ('friendships', 'Friendships'),
      ('beliefs',     'Beliefs & Values'),
      ('milestones',  'Milestones')
  ) AS u(slug, display_name)
ON CONFLICT (person_id, slug) WHERE status = 'active' DO NOTHING;

COMMIT;

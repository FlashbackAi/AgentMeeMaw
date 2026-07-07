-- ============================================================================
-- 0037_usage_events.up.sql
-- Cost/usage telemetry ledger + dashboard read views (observability dashboard).
-- The agent is the sole writer. Node's artifact-generation rows arrive via
-- POST /usage/events; Node never inserts directly. Append-only; not canonical
-- graph (no status/supersession, no embedding).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS usage_events (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    service            text          NOT NULL,            -- 'agent' | 'node'
    feature            text          NOT NULL,
    provider           text          NOT NULL,
    model              text          NOT NULL,
    input_tokens       int,
    output_tokens      int,
    cache_read_tokens  int,
    cache_write_tokens int,
    units              numeric,
    unit_type          text          NOT NULL DEFAULT 'tokens',
    cost_usd           numeric(12,6) NOT NULL,
    person_id          uuid,
    session_id         uuid,
    created_at         timestamptz   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS usage_events_created_at_idx ON usage_events (created_at DESC);
CREATE INDEX IF NOT EXISTS usage_events_feature_idx    ON usage_events (feature);
CREATE INDEX IF NOT EXISTS usage_events_model_idx      ON usage_events (provider, model);

-- Cost aggregates. Raw (not windowed): rows carry created_at, so the serving
-- layer windows in its own query without a view change.
CREATE OR REPLACE VIEW dashboard_cost_by_feature AS
SELECT feature,
       count(*)                        AS call_count,
       coalesce(sum(cost_usd), 0)      AS cost_usd,
       coalesce(sum(input_tokens), 0)  AS input_tokens,
       coalesce(sum(output_tokens), 0) AS output_tokens
FROM usage_events
GROUP BY feature;

CREATE OR REPLACE VIEW dashboard_cost_by_model AS
SELECT provider, model,
       count(*)                        AS call_count,
       coalesce(sum(cost_usd), 0)      AS cost_usd,
       coalesce(sum(input_tokens), 0)  AS input_tokens,
       coalesce(sum(output_tokens), 0) AS output_tokens
FROM usage_events
GROUP BY provider, model;

-- Operational counts (read from base tables to keep the down-migration trivial).
CREATE OR REPLACE VIEW dashboard_storybooks AS
SELECT status, collection, count(*) AS n
FROM storybooks
GROUP BY status, collection;

CREATE OR REPLACE VIEW dashboard_tributes AS
SELECT status, count(*) AS n
FROM tributes
GROUP BY status;

CREATE OR REPLACE VIEW dashboard_content_counts AS
SELECT
    (SELECT count(*) FROM moments   WHERE status = 'active') AS active_moments,
    (SELECT count(*) FROM entities  WHERE status = 'active') AS active_entities,
    (SELECT count(*) FROM threads   WHERE status = 'active') AS active_threads,
    (SELECT count(*) FROM traits    WHERE status = 'active') AS active_traits,
    (SELECT count(*) FROM questions WHERE status = 'active') AS active_questions,
    (SELECT count(*) FROM persons)                           AS persons,
    (SELECT count(*) FROM persons WHERE phase = 'starter')   AS persons_starter,
    (SELECT count(*) FROM persons WHERE phase = 'steady')    AS persons_steady;

CREATE OR REPLACE VIEW dashboard_worker_health AS
SELECT status, count(*) AS n, coalesce(max(attempts), 0) AS max_attempts
FROM extraction_outbox
GROUP BY status;

COMMIT;

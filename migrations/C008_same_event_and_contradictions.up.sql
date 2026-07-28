-- SP5: same-event linking + contradiction review records.
-- Both tables store moment ids only; provenance is resolved live via JOIN
-- to moments at read time (spec D5). A/B order is canonicalized on insert
-- (smaller UUID first) so the partial unique index collapses mirror pairs.

CREATE TABLE moment_same_event_links (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id       UUID NOT NULL REFERENCES persons(id),
    moment_a_id     UUID NOT NULL REFERENCES moments(id),
    moment_b_id     UUID NOT NULL REFERENCES moments(id),
    reason          TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    acknowledged_at TIMESTAMPTZ NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT moment_same_event_links_distinct CHECK (moment_a_id <> moment_b_id)
);

CREATE INDEX moment_same_event_links_person_status_idx
    ON moment_same_event_links (person_id, status);
CREATE INDEX moment_same_event_links_a_idx ON moment_same_event_links (moment_a_id);
CREATE INDEX moment_same_event_links_b_idx ON moment_same_event_links (moment_b_id);
CREATE UNIQUE INDEX moment_same_event_links_pair_active_uniq
    ON moment_same_event_links (moment_a_id, moment_b_id)
    WHERE status = 'active';

CREATE TABLE moment_contradictions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id       UUID NOT NULL REFERENCES persons(id),
    moment_a_id     UUID NOT NULL REFERENCES moments(id),
    moment_b_id     UUID NOT NULL REFERENCES moments(id),
    reason          TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ NULL,
    CONSTRAINT moment_contradictions_distinct CHECK (moment_a_id <> moment_b_id)
);

CREATE INDEX moment_contradictions_person_status_idx
    ON moment_contradictions (person_id, status);
CREATE INDEX moment_contradictions_a_idx ON moment_contradictions (moment_a_id);
CREATE INDEX moment_contradictions_b_idx ON moment_contradictions (moment_b_id);
CREATE UNIQUE INDEX moment_contradictions_pair_pending_uniq
    ON moment_contradictions (moment_a_id, moment_b_id)
    WHERE status = 'pending';

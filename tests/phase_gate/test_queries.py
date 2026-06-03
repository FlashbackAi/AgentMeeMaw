from __future__ import annotations

from flashback.phase_gate.queries import (
    SELECT_STEADY_CANDIDATES,
    SELECT_UNANSWERED_COVERAGE_TAP,
)


def test_coverage_tap_answered_by_filter_sql_excludes_answered_templates():
    assert "NOT EXISTS" in SELECT_UNANSWERED_COVERAGE_TAP
    assert "active_edges" in SELECT_UNANSWERED_COVERAGE_TAP
    assert "answered_by" in SELECT_UNANSWERED_COVERAGE_TAP
    assert "active_moments" in SELECT_UNANSWERED_COVERAGE_TAP
    assert "q.source = 'coverage_tap'" in SELECT_UNANSWERED_COVERAGE_TAP


def test_steady_candidate_query_excludes_recently_asked_ids():
    sql = " ".join(SELECT_STEADY_CANDIDATES.split())
    assert "NOT (q.id = ANY(%(recent_ids)s::uuid[]))" in sql
    assert "q.person_id = %(person_id)s" in sql
    assert "q.source = ANY(%(sources)s::text[])" in sql


def test_steady_candidate_query_excludes_suppressed():
    """Suppressed decisions hard-filter the candidate pool."""
    assert "active_question_decisions" in SELECT_STEADY_CANDIDATES
    assert "'suppress'" in SELECT_STEADY_CANDIDATES


def test_steady_candidate_query_generalises_suppress_by_dropped_phrase():
    """A suppressed dropped_reference excludes every active sibling sharing
    its dropped_phrase, not just the one suppressed row."""
    sql = " ".join(SELECT_STEADY_CANDIDATES.split())
    assert "dropped_phrase" in sql
    # candidate phrase compared (case/space-normalised) against a suppressed
    # decision's question phrase.
    assert "lower(btrim(sq.attributes->>'dropped_phrase'))" in sql
    assert "lower(btrim(q.attributes->>'dropped_phrase'))" in sql


def test_steady_candidate_query_generalises_suppress_by_targets_entity():
    """A suppressed entity-targeted question excludes every active sibling
    targeting the same entity."""
    sql = " ".join(SELECT_STEADY_CANDIDATES.split())
    assert "edge_type = 'targets'" in sql
    assert "qe.to_id = se.to_id" in sql


def test_steady_candidate_query_supports_exclude_skipped_param():
    """Skipped decisions are excluded only when exclude_skipped=True (3-step fallback)."""
    assert "%(exclude_skipped)s" in SELECT_STEADY_CANDIDATES
    assert "'skip'" in SELECT_STEADY_CANDIDATES


def test_coverage_tap_query_excludes_suppressed():
    assert "active_question_decisions" in SELECT_UNANSWERED_COVERAGE_TAP
    assert "'suppress'" in SELECT_UNANSWERED_COVERAGE_TAP

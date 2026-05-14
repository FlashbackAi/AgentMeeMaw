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
    assert "NOT (q.id = ANY(%(recent_ids)s::uuid[]))" in SELECT_STEADY_CANDIDATES
    assert "q.person_id = %(person_id)s" in SELECT_STEADY_CANDIDATES
    assert "q.source = ANY(%(sources)s::text[])" in SELECT_STEADY_CANDIDATES

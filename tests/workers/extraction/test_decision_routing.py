from flashback.workers.extraction.persistence import MomentDecision


def test_moment_decision_has_same_event_ids():
    d = MomentDecision(moment=object())
    assert d.same_event_ids == []
    assert d.contradicts_ids == []
    assert d.supersedes_id is None

"""Cross-contributor refinement guard (SP5 follow-up).

A `refinement` verdict supersedes (erases from active) the older moment. That
must only happen within ONE contributor's voice — a *different* contributor
adding detail about the same memory must NOT erase the first contributor's
account; it is demoted to a `same_event` link instead. Creator-era (NULL)
candidates may still be refined (single original voice).
"""

from flashback.workers.extraction.worker import refinement_supersede_allowed


def test_same_contributor_allows_supersede():
    assert refinement_supersede_allowed("user-a", "user-a") is True


def test_different_contributor_blocks_supersede():
    assert refinement_supersede_allowed("user-a", "user-b") is False


def test_creator_era_candidate_allows_supersede():
    # Existing moment is creator-era (NULL) -> a single original voice; allow.
    assert refinement_supersede_allowed("user-a", None) is True


def test_creator_era_new_against_collaborator_blocks():
    # New is creator-era (NULL) but candidate belongs to a real contributor;
    # do not let it erase that contributor's account.
    assert refinement_supersede_allowed(None, "user-b") is False


def test_both_creator_era_allows_supersede():
    assert refinement_supersede_allowed(None, None) is True

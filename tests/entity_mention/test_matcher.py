"""Word-boundary matcher tests — invariant #20's ambiguity contract.

The same-surface collision case ("Priya" as an alias of two distinct
entities) regressed silently because the longest-first span masking hid
the surface from later entries. These tests pin the fixed behavior.
"""

from __future__ import annotations

from uuid import uuid4

from flashback.entity_mention.cache import EntityNameEntry
from flashback.entity_mention.matcher import find_entity_mentions


def _entry(name: str, aliases: tuple[str, ...] = ()) -> EntityNameEntry:
    return EntityNameEntry(id=uuid4(), name=name, aliases=aliases, kind="person")


class TestFindEntityMentions:
    def test_single_match_not_ambiguous(self):
        priya = _entry("Priya")
        matches, ambiguous = find_entity_mentions(
            "I told Priya about it", [priya]
        )
        assert [m.entity_id for m in matches] == [priya.id]
        assert ambiguous is False

    def test_shared_surface_form_is_ambiguous_and_loads_both(self):
        # "Priya" resolves to two distinct active entities — the masking
        # of the first match must not hide the collision (invariant #20).
        sharma = _entry("Priya Sharma", aliases=("Priya",))
        patel = _entry("Priya Patel", aliases=("Priya",))
        matches, ambiguous = find_entity_mentions(
            "Priya came over on Sunday", [sharma, patel]
        )
        assert {m.entity_id for m in matches} == {sharma.id, patel.id}
        assert ambiguous is True

    def test_two_entities_with_identical_name_are_ambiguous(self):
        a = _entry("Comet")
        b = _entry("Comet")
        matches, ambiguous = find_entity_mentions(
            "Comet chased the ball", [a, b]
        )
        assert {m.entity_id for m in matches} == {a.id, b.id}
        assert ambiguous is True

    def test_full_name_mention_is_not_ambiguous(self):
        # The turn used the full form — the bare-alias entity must NOT
        # collide (masking still suppresses strict-substring hits).
        reddy = _entry("Chaitanya Reddy")
        other = _entry("Chaitanya")
        matches, ambiguous = find_entity_mentions(
            "Chaitanya Reddy taught me chess", [reddy, other]
        )
        assert [m.entity_id for m in matches] == [reddy.id]
        assert ambiguous is False

    def test_no_entries_no_matches(self):
        matches, ambiguous = find_entity_mentions("hello", [])
        assert matches == []
        assert ambiguous is False

"""Unit tests for gender-correct figure depiction helpers."""

from flashback.artifacts.people import (
    figure_noun,
    people_catalog_fragment,
    people_scene_fragment,
)


class TestFigureNoun:
    def test_he_and_she_map_to_nouns(self):
        assert figure_noun("he") == "a man"
        assert figure_noun("she") == "a woman"

    def test_neutral_and_unknown_return_none(self):
        # ``they`` / None / unknown stays neutral so we never push a guess.
        assert figure_noun("they") is None
        assert figure_noun(None) is None
        assert figure_noun("") is None
        assert figure_noun("xx") is None

    def test_case_and_whitespace_insensitive(self):
        assert figure_noun(" He ") == "a man"
        assert figure_noun("SHE") == "a woman"


class TestPeopleSceneFragment:
    def test_both_genders_named_by_role(self):
        out = people_scene_fragment(subject_gender="he", contributor_gender="she")
        assert "the subject as a man" in out
        assert "the contributor as a woman" in out

    def test_only_subject_known(self):
        out = people_scene_fragment(subject_gender="she", contributor_gender=None)
        assert "the subject as a woman" in out
        assert "contributor" not in out

    def test_neither_known_returns_empty(self):
        assert people_scene_fragment(subject_gender="they", contributor_gender=None) == ""
        assert people_scene_fragment(subject_gender=None, contributor_gender="they") == ""


def test_figure_noun_maps_entity_vocabulary():
    assert figure_noun("male") == "a man"
    assert figure_noun("female") == "a woman"
    assert figure_noun("MALE") == "a man"  # case-insensitive


def test_figure_noun_maps_pronoun_vocabulary():
    assert figure_noun("he") == "a man"
    assert figure_noun("she") == "a woman"


def test_figure_noun_neutral_is_none():
    assert figure_noun("they") is None
    assert figure_noun(None) is None
    assert figure_noun("aarav") is None  # a name is never a gender


def test_people_catalog_empty_when_nothing_known():
    # No subject name and nothing else known -> "" (the one case that stays
    # empty now that a named subject is always listed, even gender-unknown).
    assert people_catalog_fragment(
        subject_name="", subject_relationship="friend",
        subject_gender=None, contributor_gender=None, involved=[],
    ) == ""


def test_people_catalog_renders_known_genders():
    frag = people_catalog_fragment(
        subject_name="Meera", subject_relationship="friend",
        subject_gender="she", contributor_gender="she",
        involved=[
            {"name": "Aarav", "relationship": "her brother", "gender": "male"},
            {"name": "Priya", "relationship": "her cousin", "gender": None},
        ],
    )
    assert "Meera" in frag and "a woman" in frag
    assert "person sharing these memories" in frag
    assert "Aarav" in frag and "a man" in frag
    # An unknown-gender person is still listed by name, with no gender noun.
    assert "Priya" in frag
    assert "Priya (her cousin) is a man" not in frag
    assert "Priya (her cousin) is a woman" not in frag


def test_people_catalog_lists_unknown_gender_subject_by_name():
    frag = people_catalog_fragment(
        subject_name="Meera", subject_relationship="friend",
        subject_gender="they", contributor_gender=None,
        involved=[{"name": "Aarav", "relationship": "her brother", "gender": "male"}],
    )
    assert "Meera" in frag           # subject still listed by name
    assert "Meera (the subject" in frag
    # No gender noun invented for Meera (the fixed <people> header text uses
    # "a woman" as an illustrative example, so check Meera's own row, not a
    # bare substring search over the whole fragment).
    assert "Meera (the subject, the storyteller's friend) is a woman" not in frag
    assert "Meera (the subject, the storyteller's friend) is a man" not in frag
    assert "Aarav" in frag and "a man" in frag

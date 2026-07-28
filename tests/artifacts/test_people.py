"""Unit tests for gender-correct figure depiction helpers."""

from flashback.artifacts.people import figure_noun, people_scene_fragment


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

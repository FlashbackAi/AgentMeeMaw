from flashback.workers.producers.persistence import build_question_attributes
from flashback.workers.producers.schema import GeneratedQuestion


def test_generated_question_defaults_scope_personal():
    q = GeneratedQuestion(text="t", themes=["family"])
    assert q.scope == "personal"


def test_build_attributes_includes_normalized_scope_and_themes():
    q = GeneratedQuestion(text="t", themes=["family"],
                          attributes={"dimension": "era"}, scope="public")
    attrs = build_question_attributes(q)
    assert attrs["scope"] == "public"
    assert attrs["themes"] == ["family"]
    assert attrs["dimension"] == "era"


def test_build_attributes_defaults_scope_personal_when_unset():
    attrs = build_question_attributes(GeneratedQuestion(text="t", themes=["family"]))
    assert attrs["scope"] == "personal"

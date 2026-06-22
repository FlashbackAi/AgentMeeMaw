from flashback.workers.extraction.schema import DroppedReference


def test_dropped_reference_defaults_scope_personal():
    dr = DroppedReference(dropped_phrase="the cabin", question_text="Tell me about the cabin?", themes=["family"])
    assert dr.scope == "personal"


def test_dropped_reference_accepts_explicit_scope():
    dr = DroppedReference(dropped_phrase="rehab", question_text="What happened then?", themes=["family"], scope="private")
    assert dr.scope == "private"

from flashback.workers.extraction.schema import ExtractionResult


def _base(**over):
    kwargs = dict(moments=[], entities=[], traits=[], dropped_references=[], extraction_notes="")
    kwargs.update(over)
    return ExtractionResult(**kwargs)


def test_contributor_relationship_defaults_none():
    assert _base().contributor_relationship is None


def test_contributor_relationship_accepts_value():
    assert _base(contributor_relationship="his daughter").contributor_relationship == "his daughter"

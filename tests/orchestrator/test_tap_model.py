from uuid import uuid4

from flashback.orchestrator.protocol import Tap


def test_coverage_tap_unchanged_defaults():
    tap = Tap(question_id=uuid4(), text="Q?", dimension="sensory")
    assert tap.kind == "coverage"
    assert tap.field is None


def test_ground_truth_tap_allows_null_question_id():
    tap = Tap(
        question_id=None, text="Where did most of her life happen?",
        dimension="", kind="ground_truth", field="region",
        options=["Karimnagar", "Hyderabad", "Another state", "Outside India"],
    )
    dumped = tap.model_dump(mode="json")
    assert dumped["question_id"] is None
    assert dumped["kind"] == "ground_truth"
    assert dumped["field"] == "region"


def test_segment_anchor_tap():
    tap = Tap(question_id=None, text="About when was that?",
              dimension="", kind="segment_anchor")
    assert tap.field is None

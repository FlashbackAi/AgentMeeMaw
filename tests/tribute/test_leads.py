"""Unit tests for tribute archetype-answer leads (design 2026-06-19).

Pure logic, no DB: ranking, skip/blank filtering, JSON round-trip,
pursued-tracking, and the soft hint text.
"""

from __future__ import annotations

from flashback.tribute.leads import (
    build_leads,
    lead_hint,
    leads_from_json,
    leads_to_json,
    mark_pursued,
    pick_next_lead,
)


def _ans(qid: str, label: str = "", free: str = "", skipped: bool = False):
    d = {"question_id": qid, "question_text": f"Q for {qid}"}
    if label:
        d["option_label"] = label
    if free:
        d["free_text"] = free
    if skipped:
        d["skipped"] = True
    return d


def test_sacrifice_layer_outranks_background() -> None:
    leads = build_leads(
        [
            _ans("q1", "A village"),  # background -> low
            _ans("q10", "Sold a home"),  # given-up -> high
            _ans("q5", "Hand-me-downs"),  # mirror -> mid
        ]
    )
    assert [x.label for x in leads] == ["q10", "q5", "q1"]
    assert leads[0].value > leads[1].value > leads[2].value


def test_free_text_boosts_value() -> None:
    # Same layer, but a typed answer beats a tapped chip.
    leads = build_leads(
        [_ans("q8", "Sweets"), _ans("q8", free="He never once bought himself tea")]
    )
    typed = next(x for x in leads if x.answer.startswith("He never"))
    chip = next(x for x in leads if x.answer == "Sweets")
    assert typed.value > chip.value


def test_skipped_and_blank_answers_dropped() -> None:
    leads = build_leads(
        [
            _ans("q9", skipped=True),
            _ans("q10"),  # no label, no free_text
            _ans("q11", "Skipped meals"),
        ]
    )
    assert [x.label for x in leads] == ["q11"]


def test_free_text_wins_over_option_label() -> None:
    leads = build_leads([_ans("q10", label="Sold a home", free="Sold the house he built")])
    assert leads[0].answer == "Sold the house he built"


def test_json_roundtrip_and_pursue_flow() -> None:
    leads = build_leads([_ans("q10", "Sold a home"), _ans("q5", "Hand-me-downs")])
    raw = leads_to_json(leads)
    assert [x.label for x in leads_from_json(raw)] == ["q10", "q5"]

    first = pick_next_lead(raw)
    assert first.label == "q10"
    raw2 = mark_pursued(raw, "q10")
    # q10 now pursued -> next un-pursued is q5.
    assert pick_next_lead(raw2).label == "q5"
    raw3 = mark_pursued(raw2, "q5")
    assert pick_next_lead(raw3) is None


def test_pick_and_mark_tolerate_empty_and_garbage() -> None:
    assert pick_next_lead("") is None
    assert pick_next_lead(None) is None
    assert pick_next_lead("not json") is None
    assert leads_from_json("{}") == []
    # marking a label that doesn't exist is a no-op, not a crash.
    raw = leads_to_json(build_leads([_ans("q10", "Sold a home")]))
    assert pick_next_lead(mark_pursued(raw, "nope")).label == "q10"


def test_lead_hint_includes_question_and_answer() -> None:
    lead = build_leads([_ans("q10", "Sold a home")])[0]
    hint = lead_hint(lead)
    assert "Sold a home" in hint
    assert "Q for q10" in hint
    # Soft framing, not a directive.
    assert "gently" in hint.lower()

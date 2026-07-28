"""Pure unit checks on the tribute checklist config."""

from __future__ import annotations

from flashback.tribute.checklist import SLOT_KEYS, SLOTS


def test_slot_keys_are_the_three_expected() -> None:
    # 'appearance' was retired by migration 0050 -- it could never be filled,
    # which deadlocked the meter and blocked the message slot behind it.
    assert SLOT_KEYS == ("memories", "message", "signature")


def test_weights_sum_to_100() -> None:
    assert sum(s.weight for s in SLOTS) == 100


def test_slots_ordered_by_descending_weight() -> None:
    weights = [s.weight for s in SLOTS]
    assert weights == sorted(weights, reverse=True)


def test_every_slot_has_label_and_hint() -> None:
    for s in SLOTS:
        assert s.label.strip()
        assert s.hint.strip()

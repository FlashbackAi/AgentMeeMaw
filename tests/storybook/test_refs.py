"""Master identity refs — age anchoring, fallback, and the anti-mixup rule."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from PIL import Image

from flashback.storybook.refs import (
    AGE_STAGES,
    PRIMARY_STAGE,
    MasterRefs,
    cast_rule,
    identity_rule,
)
from flashback.storybook.script import Character


def test_identity_rule_is_appearance_only() -> None:
    r = identity_rule("Chandraiah", "Grand Father")
    assert "ONLY" in r
    assert "APPEARANCE-ONLY" in r
    assert "do not promote them into the main action" in r
    assert "Place every character exactly where the scene puts them." in r


def test_cast_rule_pins_recurring_people() -> None:
    r = cast_rule(
        [Character(name="Mokshith", who="his son",
                   appearance="short black hair, slim, clean-shaven")],
        "Chandraiah",
    )
    assert "Mokshith (his son): short black hair, slim, clean-shaven" in r
    assert "never drawn from the reference image" in r
    assert "NEVER show the same face twice" in r
    assert "do not add anyone it does not mention" in r


def test_cast_rule_empty_for_no_characters() -> None:
    assert cast_rule([], "S") == ""


def test_four_age_stages_with_mid_primary() -> None:
    assert set(AGE_STAGES) == {"child", "young", "mid", "old"}
    assert PRIMARY_STAGE == "mid"


def test_for_stage_falls_back_to_primary() -> None:
    m = MasterRefs()
    img = Image.new("RGB", (4, 4))
    m._refs = {PRIMARY_STAGE: img}
    assert m.for_stage("child") is img
    assert m.for_stage(None) is img
    assert m.for_stage("old") is img


def test_for_stage_empty_returns_none() -> None:
    assert MasterRefs().for_stage("mid") is None


def test_build_chains_stages_off_primary() -> None:
    primary = Image.new("RGB", (4, 4), (1, 1, 1))
    other = Image.new("RGB", (4, 4), (2, 2, 2))
    calls: list[dict] = []

    def fake_stage_ref(client, *, name, gt_context, stage, gender,
                       base_ref=None, photo=None, model=""):
        calls.append({"stage": stage, "base_ref": base_ref, "photo": photo})
        return primary if stage == PRIMARY_STAGE else other

    m = MasterRefs()
    with patch("flashback.storybook.refs._gen_stage_ref", fake_stage_ref):
        m.build(MagicMock(), name="S", gt_context="gt", gender=None,
                anchor_photo=None)
    assert calls[0]["stage"] == PRIMARY_STAGE
    # every later stage is conditioned on the primary so the face carries
    later = [c for c in calls if c["stage"] != PRIMARY_STAGE]
    assert len(later) == 3
    assert all(c["base_ref"] is primary for c in later)
    assert m.for_stage("child") is other
    assert m.for_stage("mid") is primary


def test_build_failed_stage_falls_back_to_primary() -> None:
    primary = Image.new("RGB", (4, 4))

    def fake_stage_ref(client, *, name, gt_context, stage, gender,
                       base_ref=None, photo=None, model=""):
        return primary if stage == PRIMARY_STAGE else None

    m = MasterRefs()
    with patch("flashback.storybook.refs._gen_stage_ref", fake_stage_ref):
        m.build(MagicMock(), name="S", gt_context="gt", gender=None,
                anchor_photo=None)
    assert m.for_stage("old") is primary

"""DB tests for the two-meter tribute_status view (design 2026-07-22).

Two kinds of tribute row:
  * CAMPAIGN (campaign_id set) — memories 50 / message 35 / signature 15 (the
    archetype answer-floor lifts memories); appearance retired as a scored slot
    (migration 0050) but its require_appearance gate on `ready` still honored.
  * STANDALONE (campaign_id NULL) — no message slot; memories-led smooth
    percent (memories 85 / signature 15); unlocks on the story floor alone.
"""

from __future__ import annotations

import json

from flashback.tribute.config_schema import CampaignConfig
from flashback.tribute.progress import fetch_tribute_progress_sync
from flashback.tribute.repository import insert_tribute_sync, set_message_sync


def _slot(progress, key: str):
    return next(s for s in progress.slots if s.key == key)


def _has_slot(progress, key: str) -> bool:
    return any(s.key == key for s in progress.slots)


def _add_qualifying_moment(cur, person_id: str, title: str) -> None:
    cur.execute(
        """
        INSERT INTO moments (person_id, title, narrative, sensory_details)
        VALUES (%s, %s, %s, %s)
        """,
        (person_id, title, "a narrative", "the smell of diesel and rain"),
    )


def _add_deep_moment(cur, person_id: str, title: str) -> None:
    # >80 chars of sensory + a year time_anchor => depth bonuses (0030).
    long_sensory = (
        "the smell of diesel and rain on the platform, his cracked hands, "
        "the cold steel bench, the 4 a.m. dark before the first train"
    )
    cur.execute(
        """
        INSERT INTO moments (person_id, title, narrative, sensory_details,
                             time_anchor)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (person_id, title, "a narrative", long_sensory, json.dumps({"year": 1974})),
    )


def _set_appearance_ground_truth(cur, person_id: str) -> None:
    gt = {
        "region": {"value": "South India", "provenance": "tap",
                   "confidence": "high", "updated_at": "2026-06-14T00:00:00Z"},
        "birth_era": {"value": "1950s", "provenance": "onboarding",
                      "confidence": "medium", "updated_at": "2026-06-14T00:00:00Z"},
        "attire": {"value": "white cotton shirt", "provenance": "inferred",
                   "confidence": "medium", "updated_at": "2026-06-14T00:00:00Z"},
    }
    cur.execute(
        "UPDATE persons SET ground_truth = %s WHERE id = %s",
        (json.dumps(gt), person_id),
    )


def _add_trait(cur, person_id: str) -> None:
    cur.execute(
        "INSERT INTO traits (person_id, name, description, status) "
        "VALUES (%s, 'patient', NULL, 'active')",
        (person_id,),
    )


def _make_tribute_theme(cur, person_id: str, answers: list[dict]) -> str:
    cur.execute(
        """
        INSERT INTO themes (person_id, kind, slug, display_name, state,
                            status, archetype_answers)
        VALUES (%s, 'tribute', 'tribute', 'A Tribute', 'unlocked', 'active', %s)
        RETURNING id::text
        """,
        (person_id, json.dumps(answers)),
    )
    return cur.fetchone()[0]


def _make_campaign(cur, person_id: str, *, require_appearance=False,
                   require_signature=False) -> str:
    # Unique slug per person: the active-slug index is global, and the session
    # DB is shared across tests.
    slug = f"fd_{str(person_id).replace('-', '')[:12]}"
    cur.execute(
        """
        INSERT INTO tribute_campaigns
            (slug, display_name, require_appearance, require_signature)
        VALUES (%s, 'FD Test', %s, %s)
        RETURNING id::text
        """,
        (slug, require_appearance, require_signature),
    )
    return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Campaign branch (campaign_id set): four weighted slots + message + floor
# ---------------------------------------------------------------------------

def test_campaign_answer_floor_lifts_percent(db_pool, make_person) -> None:
    person_id = make_person("Dad")
    answers = [
        {"question_id": "q10", "option_label": "Sold a home"},
        {"question_id": "q11", "option_label": "Skipped meals"},
        {"question_id": "q14", "free_text": "I love you"},
        {"question_id": "q9", "skipped": True},  # no choice -> not counted
    ]
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            theme_id = _make_tribute_theme(cur, person_id, answers)
            cid = _make_campaign(cur, person_id)
            tribute_id = insert_tribute_sync(
                cur, person_id=person_id, theme_id=theme_id, campaign_id=cid
            )
            conn.commit()
            progress = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert progress.kind == "campaign"
    assert progress.answered_layers == 3
    assert progress.percent == 11  # round(3/14 * 50)
    assert progress.ready is False


def test_campaign_answer_floor_caps_at_16(db_pool, make_person) -> None:
    person_id = make_person("Dad")
    answers = [{"question_id": f"q{i}", "option_label": "x"} for i in range(1, 15)]
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            theme_id = _make_tribute_theme(cur, person_id, answers)
            cid = _make_campaign(cur, person_id)
            tribute_id = insert_tribute_sync(
                cur, person_id=person_id, theme_id=theme_id, campaign_id=cid
            )
            conn.commit()
            progress = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert progress.answered_layers == 14
    assert progress.percent == 20  # capped floor (0.4 * 50), no other slots


def test_campaign_depth_weighting_two_vivid_moments_fill_memories(db_pool, make_person) -> None:
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cid = _make_campaign(cur, person_id)
            tribute_id = insert_tribute_sync(cur, person_id=person_id, campaign_id=cid)
            _add_deep_moment(cur, person_id, "Deep A")
            _add_deep_moment(cur, person_id, "Deep B")
            conn.commit()
            progress = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert progress.percent == 50  # depth-weighted memories maxed
    assert _slot(progress, "memories").filled is False  # only 2 raw stories
    assert progress.ready is False


def test_campaign_fills_each_slot_to_ready(db_pool, make_person) -> None:
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cid = _make_campaign(cur, person_id)
            tribute_id = insert_tribute_sync(cur, person_id=person_id, campaign_id=cid)
            for i in range(3):
                _add_qualifying_moment(cur, person_id, f"Memory {i}")
            _set_appearance_ground_truth(cur, person_id)  # feeds art, not scored
            _add_trait(cur, person_id)
            set_message_sync(cur, tribute_id=tribute_id, message_text="Thank you, Dad.")
            conn.commit()
            progress = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert progress.kind == "campaign"
    assert _has_slot(progress, "message")
    assert not _has_slot(progress, "appearance")  # retired slot (0050)
    assert all(_slot(progress, k).filled for k in
               ("memories", "message", "signature"))
    assert progress.percent == 100  # 50 + 35 + 15
    assert progress.ready is True


def test_campaign_partial_memories_scale_weight(db_pool, make_person) -> None:
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cid = _make_campaign(cur, person_id)
            tribute_id = insert_tribute_sync(cur, person_id=person_id, campaign_id=cid)
            _add_qualifying_moment(cur, person_id, "Memory A")
            _add_qualifying_moment(cur, person_id, "Memory B")
            conn.commit()
            progress = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert progress.percent == 33  # round(2/3 * 50)
    assert progress.ready is False


def test_campaign_require_appearance_gates_ready(db_pool, make_person) -> None:
    # A campaign with require_appearance=true won't be ready without appearance,
    # even with stories + message (the soft slot joins the hard gate).
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cid = _make_campaign(cur, person_id, require_appearance=True)
            tribute_id = insert_tribute_sync(cur, person_id=person_id, campaign_id=cid)
            for i in range(3):
                _add_qualifying_moment(cur, person_id, f"Memory {i}")
            set_message_sync(cur, tribute_id=tribute_id, message_text="Thanks.")
            conn.commit()
            before = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)
            _set_appearance_ground_truth(cur, person_id)
            conn.commit()
            after = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert before.ready is False   # stories + message present, appearance required
    assert after.ready is True     # appearance now present


def test_campaign_skins_title_and_message_hint(db_pool, make_person) -> None:
    person_id = make_person("Dad")
    campaign = CampaignConfig(
        id="c-fd", slug="fathers_day_2026", display_name="A Letter to Dad",
        message_card_copy="If he could hear one thing from you — what is it?",
        archetype_extra_context="", video_target_seconds=45, featured=True,
        active_start=None, active_end=None, archetype_bank_override=None,
        deage_cover_override=True, visual_theme_id=None,
        closing_card_copy=None, state="published", version=1,
    )
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cid = _make_campaign(cur, person_id)
            tribute_id = insert_tribute_sync(cur, person_id=person_id, campaign_id=cid)
            conn.commit()
            progress = fetch_tribute_progress_sync(
                cur, tribute_id=tribute_id, campaign=campaign
            )

    assert progress.kind == "campaign"
    assert progress.title == campaign.display_name
    assert _slot(progress, "message").hint == campaign.message_card_copy
    assert not _has_slot(progress, "appearance")  # retired slot (0050)


# ---------------------------------------------------------------------------
# Standalone branch (campaign_id NULL): memories-led, no message
# ---------------------------------------------------------------------------

def test_standalone_empty_is_zero_and_has_no_message_slot(db_pool, make_person) -> None:
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            tribute_id = insert_tribute_sync(cur, person_id=person_id)  # campaign_id NULL
            conn.commit()
            progress = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert progress.kind == "standalone"
    assert progress.percent == 0
    assert progress.ready is False
    assert not _has_slot(progress, "message")   # simplified: no message
    assert {s.key for s in progress.slots} == {"memories", "signature"}


def test_standalone_memories_led_percent(db_pool, make_person) -> None:
    # 3 plain qualifying moments => score 3.0 => round(3/5 * 85) = 51.
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            tribute_id = insert_tribute_sync(cur, person_id=person_id)
            for i in range(3):
                _add_qualifying_moment(cur, person_id, f"Memory {i}")
            conn.commit()
            progress = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert progress.percent == 51
    assert progress.ready is True   # story floor met, no message required


def test_standalone_ready_on_stories_without_message(db_pool, make_person) -> None:
    # Unlocks on stories alone — signature is soft (not required); appearance
    # is no longer a slot at all (0050).
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            tribute_id = insert_tribute_sync(cur, person_id=person_id)
            for i in range(3):
                _add_qualifying_moment(cur, person_id, f"Memory {i}")
            conn.commit()
            progress = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert progress.ready is True
    assert not _has_slot(progress, "appearance")  # retired slot (0050)
    assert _slot(progress, "signature").filled is False


def test_standalone_soft_slots_raise_percent_to_full(db_pool, make_person) -> None:
    # 3 deep moments (score 6 -> capped 5 -> 85) + signature (15) = 100.
    # Appearance is set but no longer scored (0050).
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            tribute_id = insert_tribute_sync(cur, person_id=person_id)
            for i in range(3):
                _add_deep_moment(cur, person_id, f"Deep {i}")
            _set_appearance_ground_truth(cur, person_id)  # feeds art, not scored
            _add_trait(cur, person_id)
            conn.commit()
            progress = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert progress.percent == 100
    assert progress.ready is True


def test_standalone_title_and_next(db_pool, make_person) -> None:
    person_id = make_person("Dad")
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            tribute_id = insert_tribute_sync(cur, person_id=person_id)
            _add_qualifying_moment(cur, person_id, "Memory A")
            conn.commit()
            progress = fetch_tribute_progress_sync(cur, tribute_id=tribute_id)

    assert progress.title == "A Tribute"
    assert progress.next_key == "memories"   # 1 of 3, first unfilled
    mem = _slot(progress, "memories")
    assert (mem.count, mem.target) == (1, 3)

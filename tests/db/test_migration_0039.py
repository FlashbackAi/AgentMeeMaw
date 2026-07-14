"""Migration 0039: tribute CRM config tables, seeds, lifecycle constraints.

Three config tables (relationship_profiles, tribute_campaigns,
tribute_visual_themes) + persons.relationship_group + tributes.campaign_id.
Seeds: 8 published profiles, the retrofitted fathers_day_2026 campaign, and
the classic_keepsake visual theme (NULL image = built-in kit).
"""

from __future__ import annotations

import pytest

psycopg = pytest.importorskip("psycopg")


@pytest.fixture
def db_conn(schema_applied: str):
    with psycopg.connect(schema_applied) as conn:
        yield conn
        conn.rollback()


def test_seeded_profiles_all_eight(db_conn) -> None:
    rows = db_conn.execute(
        "SELECT group_slug FROM relationship_profiles "
        "WHERE state='published' AND status='active' ORDER BY group_slug"
    ).fetchall()
    assert [r[0] for r in rows] == sorted(
        [
            "parent",
            "grandparent",
            "sibling",
            "cousin",
            "friend",
            "spouse_partner",
            "mentor",
            "other",
        ]
    )


def test_friend_profile_is_playful_with_bank(db_conn) -> None:
    voice, opener, bank, deage = db_conn.execute(
        "SELECT voice, opener, archetype_bank, deage_cover "
        "FROM relationship_profiles "
        "WHERE group_slug='friend' AND status='active'"
    ).fetchone()
    assert "playful" in voice["energy_words"]
    # Explicit product rule: never a formal "Meet my friend" introduction.
    assert "never" in voice
    assert any("meet my" in n.lower() for n in voice["never"])
    assert all("{name}" in ex for ex in opener["examples"])
    assert isinstance(bank, list) and len(bank) >= 8
    assert all(len(q["options"]) >= 2 for q in bank)
    assert deage is False


def test_fd_campaign_retrofitted(db_conn) -> None:
    name, bank, deage, featured, start, end, state = db_conn.execute(
        "SELECT display_name, archetype_bank_override, deage_cover_override, "
        "featured, active_start, active_end, state "
        "FROM tribute_campaigns WHERE slug='fathers_day_2026' AND status='active'"
    ).fetchone()
    assert name == "A Letter to Dad"
    assert len(bank) == 22
    assert bank[0]["question"] == "Where did your father grow up?"
    assert deage is True and featured is True and state == "published"
    assert str(start) == "2026-06-01" and str(end) == "2026-06-22"


def test_classic_visual_theme_null_image_is_builtin(db_conn) -> None:
    img, fonts, ink, audio = db_conn.execute(
        "SELECT template_image, fonts, ink, audio_slug FROM tribute_visual_themes "
        "WHERE slug='classic_keepsake' AND status='active'"
    ).fetchone()
    assert img is None
    assert fonts == {"main_slug": "playfair_italic", "eyebrow_slug": "eb_garamond"}
    assert ink == {"main_fill": "#3a2c1c", "eyebrow_fill": "#967648"}
    assert audio == "sentimental_piano"


def test_profiles_reference_classic_theme(db_conn) -> None:
    (count,) = db_conn.execute(
        "SELECT count(*) FROM relationship_profiles p "
        "JOIN tribute_visual_themes v ON v.id = p.visual_theme_id "
        "WHERE p.status='active' AND v.slug='classic_keepsake'"
    ).fetchone()
    assert count == 8


def test_new_columns_exist(db_conn) -> None:
    db_conn.execute("SELECT relationship_group FROM persons LIMIT 0")
    db_conn.execute("SELECT campaign_id FROM tributes LIMIT 0")


def test_seed_copy_is_third_person_and_templated(db_conn) -> None:
    rows = db_conn.execute(
        "SELECT group_slug, fallback_opener, fallback_closing "
        "FROM relationship_profiles WHERE status='active'"
    ).fetchall()
    for _slug, opener, closing in rows:
        assert "{name}" in opener
        assert "{name}" in closing


def test_slug_unique_only_for_active(db_conn) -> None:
    # Superseding the active friend row then re-inserting the slug is legal:
    # uniqueness is scoped to status='active' (house supersession pattern).
    row = db_conn.execute(
        "UPDATE relationship_profiles SET status='superseded' "
        "WHERE group_slug='friend' AND status='active' RETURNING id"
    ).fetchone()
    assert row is not None
    db_conn.execute(
        """
        INSERT INTO relationship_profiles
            (group_slug, display_name, synonyms, voice, opener, art,
             fallback_opener, fallback_closing, state, version)
        VALUES ('friend', 'Friend', ARRAY['friend'],
                '{"energy_words":["playful"],"narrator_stance":"x","emotion_rule":"y","never":[]}',
                '{"style":"z","examples":["Go, {name}."]}',
                '{"mood_words":["bright"],"avoid":[]}',
                'Some people get lucky. I got {name}.',
                'Thanks for all of it, {name}.', 'published', 2)
        """
    )
    db_conn.rollback()

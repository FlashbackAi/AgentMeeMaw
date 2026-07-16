"""Campaign relationship targeting (migration 0041).

A campaign scoped to specific relationship groups applies only to matching
legacies: the public list scopes is_active/active_featured_slug per person,
and untargeted campaigns keep the pre-0041 behavior.
"""

from __future__ import annotations

from datetime import date, timedelta

from flashback.tribute.config_schema import (
    NEUTRAL_CAMPAIGN,
    CampaignConfig,
    campaign_applies,
    validate_campaign_payload,
)
from tests.http.conftest import admin_headers, auth_headers

_H = admin_headers(user="targeting@flashback")


def _campaign(groups=()) -> CampaignConfig:
    return CampaignConfig(
        id="c1", slug="s", display_name="D", message_card_copy=None,
        archetype_extra_context="", video_target_seconds=None,
        featured=True, active_start=None, active_end=None,
        archetype_bank_override=None, deage_cover_override=None,
        visual_theme_id=None, closing_card_copy=None, state="published",
        version=1, relationship_groups=tuple(groups),
    )


def test_campaign_applies_rules() -> None:
    assert campaign_applies(_campaign(), "parent") is True
    assert campaign_applies(_campaign(), None) is True
    assert campaign_applies(NEUTRAL_CAMPAIGN, "friend") is True
    targeted = _campaign(("friend", "cousin"))
    assert campaign_applies(targeted, "friend") is True
    assert campaign_applies(targeted, "parent") is False
    assert campaign_applies(targeted, None) is False  # unclassified


def test_relationship_groups_payload_validation() -> None:
    ok = {"slug": "s", "display_name": "D",
          "relationship_groups": ["friend", "cousin"]}
    assert validate_campaign_payload(ok) == []
    bad = {"slug": "s", "display_name": "D", "relationship_groups": "friend"}
    assert any("relationship_groups" in e
               for e in validate_campaign_payload(bad))


async def test_targeted_campaign_is_active_only_for_matching_group(
    client_with_db, async_db_pool
) -> None:
    today = date.today()
    async with async_db_pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO tribute_campaigns (slug, display_name, "
                    "featured, active_start, active_end, "
                    "relationship_groups, state) "
                    "VALUES ('friends_only_test', 'Friends Only', TRUE, "
                    "%s, %s, %s, 'published') RETURNING id::text",
                    (today - timedelta(days=1), today + timedelta(days=1),
                     ["friend"]),
                )
                (campaign_id,) = await cur.fetchone()
                await cur.execute(
                    "INSERT INTO persons (name, relationship_group) "
                    "VALUES ('Dad', 'parent') RETURNING id::text"
                )
                (father_id,) = await cur.fetchone()
                await cur.execute(
                    "INSERT INTO persons (name, relationship_group) "
                    "VALUES ('Bestie', 'friend') RETURNING id::text"
                )
                (friend_id,) = await cur.fetchone()

    try:
        # global view (no person): listed, active by window+featured
        pub = await client_with_db.get(
            "/tribute-campaigns", headers=auth_headers()
        )
        row = next(c for c in pub.json()["campaigns"]
                   if c["slug"] == "friends_only_test")
        assert row["relationship_groups"] == ["friend"]

        # father legacy: not active, never the featured slug
        for_father = await client_with_db.get(
            "/tribute-campaigns", params={"person_id": father_id},
            headers=auth_headers(),
        )
        body = for_father.json()
        row = next(c for c in body["campaigns"]
                   if c["slug"] == "friends_only_test")
        assert row["is_active"] is False
        assert body["active_featured_slug"] != "friends_only_test"

        # friend legacy: active + featured
        for_friend = await client_with_db.get(
            "/tribute-campaigns", params={"person_id": friend_id},
            headers=auth_headers(),
        )
        body = for_friend.json()
        row = next(c for c in body["campaigns"]
                   if c["slug"] == "friends_only_test")
        assert row["is_active"] is True
        assert body["active_featured_slug"] == "friends_only_test"
    finally:
        async with async_db_pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(
                        "DELETE FROM tribute_campaigns WHERE id = %s",
                        (campaign_id,),
                    )


async def test_crm_roundtrips_relationship_groups(client_with_db) -> None:
    made = await client_with_db.post(
        "/admin/tribute_config/tribute_campaigns",
        json={"payload": {"slug": "rg_roundtrip_test", "display_name": "R",
                          "relationship_groups": ["friend", "cousin"]}},
        headers=_H,
    )
    assert made.status_code == 200, made.text
    listing = await client_with_db.get(
        "/admin/tribute_config/tribute_campaigns", headers=_H
    )
    row = next(r for r in listing.json()["rows"]
               if r["slug"] == "rg_roundtrip_test")
    assert row["relationship_groups"] == ["friend", "cousin"]
    # cleanup via the draft hard-delete
    resp = await client_with_db.delete(
        f"/admin/tribute_config/tribute_campaigns/{made.json()['id']}",
        headers=_H,
    )
    assert resp.status_code == 200, resp.text

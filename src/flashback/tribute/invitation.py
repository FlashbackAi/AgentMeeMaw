"""Resolve the message-invitation question for a tribute.

One chain, used by every surface that shows the "say it to them" question
(the in-chat warm-climax card, the live meter, GET /tributes/{id}/progress,
and the direct tribute-card capture endpoint):

    campaign copy  ->  relationship-profile copy  ->  neutral line

The campaign the tribute was created under wins (stamped on the row at
entry); a caller-supplied slug is the fallback for pre-0039 tributes.
Best-effort: any failure lands on the neutral line — the question can
never block a surface.
"""

from __future__ import annotations

import structlog

from flashback.tribute.config_repository import (
    fetch_campaign_by_id,
    fetch_profile_by_group,
    resolve_campaign_db,
)
from flashback.tribute.repository import fetch_tribute_campaign_id_async
from flashback.tribute.theme import MESSAGE_INVITATION_COPY

log = structlog.get_logger("flashback.tribute.invitation")


async def resolve_invitation_copy(
    cur,
    *,
    tribute_id: str,
    person_id: str,
    wm_campaign_slug: str | None = None,
) -> str:
    """campaign copy -> profile copy -> neutral. Never raises."""
    try:
        campaign = None
        campaign_id = await fetch_tribute_campaign_id_async(
            cur, tribute_id=tribute_id
        )
        if campaign_id:
            campaign = await fetch_campaign_by_id(cur, campaign_id)
        if campaign is None:
            campaign = await resolve_campaign_db(cur, wm_campaign_slug)
        if campaign.message_card_copy:
            return campaign.message_card_copy

        await cur.execute(
            "SELECT relationship_group FROM persons WHERE id = %s",
            (str(person_id),),
        )
        row = await cur.fetchone()
        group = (row[0] if row else None) or "other"
        profile = await fetch_profile_by_group(cur, group)
        if profile is not None and profile.message_invitation_copy:
            return profile.message_invitation_copy
    except Exception:
        log.warning("invitation.copy_resolution_failed", exc_info=True)
    return MESSAGE_INVITATION_COPY

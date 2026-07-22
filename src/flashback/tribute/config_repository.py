"""Repository for the tribute CRM config tables (migration 0039).

One lifecycle for all three tables: ``state`` (draft | published | archived)
is the CRM lifecycle — runtime resolution reads published only; edits use
the house supersession pattern (old row -> status='superseded', new row with
version+1). Rollback republishes a superseded row's content as a fresh
active row, so history is append-only and every render snapshot's pinned
row id stays resolvable forever.

All functions take an open async cursor; transaction scope belongs to the
caller (routes wrap conn.transaction()).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from psycopg.types.json import Json

from flashback.tribute.config_schema import (
    NEUTRAL_CAMPAIGN,
    CampaignConfig,
    ProfileConfig,
    VisualThemeConfig,
)

ConfigTable = Literal[
    "relationship_profiles", "tribute_campaigns", "tribute_visual_themes"
]

# Column registries: payload keys accepted for create/edit, with the JSONB
# members that need Json() adaptation and the DATE members that accept ISO
# strings from the CRM.
_COLUMNS: dict[str, tuple[str, ...]] = {
    "relationship_profiles": (
        "group_slug",
        "display_name",
        "synonyms",
        "voice",
        "opener",
        "art",
        "fallback_opener",
        "fallback_closing",
        "archetype_bank",
        "message_invitation_copy",
        "deage_cover",
        "video_target_seconds",
        "visual_theme_id",
        "narrative",
    ),
    "tribute_campaigns": (
        "slug",
        "display_name",
        "message_card_copy",
        "archetype_extra_context",
        "video_target_seconds",
        "featured",
        "active_start",
        "active_end",
        "archetype_bank_override",
        "deage_cover_override",
        "visual_theme_id",
        "closing_card_copy",
        "relationship_groups",
        "narrative_override",
        "require_appearance",
        "require_signature",
    ),
    "tribute_visual_themes": (
        "slug",
        "display_name",
        "template_image",
        "template_mime",
        "fonts",
        "ink",
        "audio_slug",
        "layout_palette",
        "layout_pins",
        "pacing",
        "motion_preset",
        "render_engine",
    ),
}
_JSONB_COLUMNS = {
    "voice",
    "opener",
    "art",
    "narrative",
    "narrative_override",
    "archetype_bank",
    "archetype_bank_override",
    "fonts",
    "ink",
    "layout_pins",
    "pacing",
}
_DATE_COLUMNS = {"active_start", "active_end"}
_SLUG_COLUMN = {
    "relationship_profiles": "group_slug",
    "tribute_campaigns": "slug",
    "tribute_visual_themes": "slug",
}
# The safety-floor profile: runtime falls back to it, so it can never leave
# the published pool (spec §6.5).
_PROTECTED = ("relationship_profiles", "other")


def _adapt(col: str, value: Any) -> Any:
    if value is None:
        return None
    if col in _JSONB_COLUMNS:
        return Json(value)
    if col in _DATE_COLUMNS and isinstance(value, str):
        return datetime.strptime(value, "%Y-%m-%d").date()
    return value


def _row_to_profile(row) -> ProfileConfig:
    return ProfileConfig(
        id=row[0],
        group_slug=row[1],
        display_name=row[2],
        synonyms=tuple(row[3] or ()),
        voice=row[4] or {},
        opener=row[5] or {},
        art=row[6] or {},
        fallback_opener=row[7],
        fallback_closing=row[8],
        archetype_bank=row[9],
        message_invitation_copy=row[10],
        deage_cover=row[11],
        video_target_seconds=row[12],
        visual_theme_id=str(row[13]) if row[13] else None,
        state=row[14],
        version=row[15],
        narrative=row[16] or {},
    )


_PROFILE_COLS = (
    "id::text, group_slug, display_name, synonyms, voice, opener, art, "
    "fallback_opener, fallback_closing, archetype_bank, "
    "message_invitation_copy, deage_cover, video_target_seconds, "
    "visual_theme_id, state, version, narrative"
)


def _row_to_campaign(row) -> CampaignConfig:
    return CampaignConfig(
        id=row[0],
        slug=row[1],
        display_name=row[2],
        message_card_copy=row[3],
        archetype_extra_context=row[4] or "",
        video_target_seconds=row[5],
        featured=row[6],
        active_start=row[7],
        active_end=row[8],
        archetype_bank_override=row[9],
        deage_cover_override=row[10],
        visual_theme_id=str(row[11]) if row[11] else None,
        closing_card_copy=row[12],
        state=row[13],
        version=row[14],
        relationship_groups=tuple(row[15] or ()),
        narrative_override=row[16] or {},
        require_appearance=bool(row[17]),
        require_signature=bool(row[18]),
    )


_CAMPAIGN_COLS = (
    "id::text, slug, display_name, message_card_copy, archetype_extra_context, "
    "video_target_seconds, featured, active_start, active_end, "
    "archetype_bank_override, deage_cover_override, visual_theme_id, "
    "closing_card_copy, state, version, relationship_groups, narrative_override, "
    "require_appearance, require_signature"
)


def _row_to_visual_theme(row) -> VisualThemeConfig:
    return VisualThemeConfig(
        id=row[0],
        slug=row[1],
        display_name=row[2],
        has_image=row[3],
        template_mime=row[4],
        fonts=row[5] or {},
        ink=row[6] or {},
        audio_slug=row[7],
        state=row[8],
        version=row[9],
        layout_palette=list(row[10] or []),
        layout_pins=row[11] or {},
        pacing=row[12] or {},
        motion_preset=row[13] or "",
        render_engine=row[14] or "",
    )


_VISUAL_COLS = (
    "id::text, slug, display_name, (template_image IS NOT NULL) AS has_image, "
    "template_mime, fonts, ink, audio_slug, state, version, "
    "layout_palette, layout_pins, pacing, motion_preset, render_engine"
)


# ---------------------------------------------------------------------------
# Fetch / resolution (runtime + admin)
# ---------------------------------------------------------------------------


async def fetch_profile_by_group(
    cur, group_slug: str, *, published_only: bool = True
) -> ProfileConfig | None:
    state_filter = "AND state = 'published'" if published_only else ""
    await cur.execute(
        f"SELECT {_PROFILE_COLS} FROM relationship_profiles "
        f"WHERE group_slug = %s AND status = 'active' {state_filter}",
        (group_slug,),
    )
    row = await cur.fetchone()
    return _row_to_profile(row) if row else None


async def fetch_all_published_profiles(cur) -> list[ProfileConfig]:
    await cur.execute(
        f"SELECT {_PROFILE_COLS} FROM relationship_profiles "
        "WHERE status = 'active' AND state = 'published' ORDER BY group_slug"
    )
    return [_row_to_profile(r) for r in await cur.fetchall()]


async def fetch_profile_by_id(cur, profile_id) -> ProfileConfig | None:
    await cur.execute(
        f"SELECT {_PROFILE_COLS} FROM relationship_profiles WHERE id = %s",
        (str(profile_id),),
    )
    row = await cur.fetchone()
    return _row_to_profile(row) if row else None


async def fetch_campaign_by_slug(
    cur, slug: str, *, published_only: bool = True
) -> CampaignConfig | None:
    state_filter = "AND state = 'published'" if published_only else ""
    await cur.execute(
        f"SELECT {_CAMPAIGN_COLS} FROM tribute_campaigns "
        f"WHERE slug = %s AND status = 'active' {state_filter}",
        (slug,),
    )
    row = await cur.fetchone()
    return _row_to_campaign(row) if row else None


async def fetch_campaign_by_id(cur, campaign_id) -> CampaignConfig | None:
    await cur.execute(
        f"SELECT {_CAMPAIGN_COLS} FROM tribute_campaigns WHERE id = %s",
        (str(campaign_id),),
    )
    row = await cur.fetchone()
    return _row_to_campaign(row) if row else None


async def fetch_visual_theme_by_slug(cur, slug: str) -> VisualThemeConfig | None:
    await cur.execute(
        f"SELECT {_VISUAL_COLS} FROM tribute_visual_themes "
        "WHERE slug = %s AND status = 'active'",
        (slug,),
    )
    row = await cur.fetchone()
    return _row_to_visual_theme(row) if row else None


async def fetch_visual_theme_by_id(cur, theme_id) -> VisualThemeConfig | None:
    await cur.execute(
        f"SELECT {_VISUAL_COLS} FROM tribute_visual_themes WHERE id = %s",
        (str(theme_id),),
    )
    row = await cur.fetchone()
    return _row_to_visual_theme(row) if row else None


async def fetch_visual_theme_image(cur, theme_id) -> tuple[bytes, str] | None:
    await cur.execute(
        "SELECT template_image, template_mime FROM tribute_visual_themes "
        "WHERE id = %s AND template_image IS NOT NULL",
        (str(theme_id),),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return bytes(row[0]), row[1] or "image/jpeg"


async def resolve_campaign_db(cur, slug: str | None) -> CampaignConfig:
    """Slug -> published campaign, else the code-side neutral null-object."""
    if not slug or slug == "default":
        return NEUTRAL_CAMPAIGN
    campaign = await fetch_campaign_by_slug(cur, slug)
    return campaign if campaign is not None else NEUTRAL_CAMPAIGN


async def active_featured_campaign_db(cur, today: date) -> CampaignConfig | None:
    await cur.execute(
        f"SELECT {_CAMPAIGN_COLS} FROM tribute_campaigns "
        "WHERE status = 'active' AND state = 'published' AND featured "
        "AND active_start IS NOT NULL AND active_end IS NOT NULL "
        "AND active_start <= %s AND active_end >= %s "
        "ORDER BY active_start LIMIT 1",
        (today, today),
    )
    row = await cur.fetchone()
    return _row_to_campaign(row) if row else None


# ---------------------------------------------------------------------------
# Admin CRUD + lifecycle
# ---------------------------------------------------------------------------


async def active_slug_state(cur, table: ConfigTable, slug: str) -> str | None:
    """The ``state`` of the active row holding ``slug``, or None if free."""
    slug_col = _SLUG_COLUMN[table]
    await cur.execute(
        f"SELECT state FROM {table} WHERE {slug_col} = %s AND status = 'active'",
        (slug,),
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def supersede_active_slug(
    cur, table: ConfigTable, slug: str, *, updated_by: str
) -> bool:
    """Free up ``slug`` by superseding its active row. True if one existed.

    Used by candidate regeneration (replace-draft semantics): a redo with
    the same slug supersedes the stale draft instead of crashing on the
    active-slug unique index. History is preserved — superseded rows stay
    browsable and any snapshot pinning the old row id still resolves.
    """
    slug_col = _SLUG_COLUMN[table]
    await cur.execute(
        f"UPDATE {table} SET status = 'superseded', updated_by = %s "
        f"WHERE {slug_col} = %s AND status = 'active' RETURNING id",
        (updated_by, slug),
    )
    return await cur.fetchone() is not None


async def list_rows(
    cur,
    table: ConfigTable,
    *,
    include_archived: bool = False,
    include_superseded: bool = False,
) -> list[dict]:
    cols = {
        "relationship_profiles": _PROFILE_COLS,
        "tribute_campaigns": _CAMPAIGN_COLS,
        "tribute_visual_themes": _VISUAL_COLS,
    }[table]
    filters = []
    if not include_superseded:
        filters.append("status = 'active'")
    if not include_archived:
        filters.append("state != 'archived'")
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    await cur.execute(
        f"SELECT {cols}, status, updated_by, updated_at::text FROM {table} "
        f"{where} ORDER BY {_SLUG_COLUMN[table]}, version"
    )
    rows = await cur.fetchall()
    names = [d.name for d in cur.description]
    return [dict(zip(names, r)) for r in rows]


async def create_row(
    cur, table: ConfigTable, payload: dict, *, updated_by: str
) -> str:
    cols = [c for c in _COLUMNS[table] if c in payload]
    if _SLUG_COLUMN[table] not in cols:
        raise ValueError(f"{_SLUG_COLUMN[table]} is required")
    col_sql = ", ".join(cols)
    ph = ", ".join(["%s"] * len(cols))
    await cur.execute(
        f"INSERT INTO {table} ({col_sql}, state, version, updated_by) "
        f"VALUES ({ph}, 'draft', 1, %s) RETURNING id::text",
        [_adapt(c, payload[c]) for c in cols] + [updated_by],
    )
    return (await cur.fetchone())[0]


async def supersede_edit(
    cur, table: ConfigTable, row_id, payload: dict, *, updated_by: str
) -> str:
    """Old active row -> superseded; new row carries edits + old fields."""
    await cur.execute(
        f"UPDATE {table} SET status = 'superseded', updated_by = %s "
        "WHERE id = %s AND status = 'active' RETURNING id",
        (updated_by, str(row_id)),
    )
    if await cur.fetchone() is None:
        raise LookupError(f"{table}: no active row {row_id}")

    all_cols = list(_COLUMNS[table])
    edited = [c for c in all_cols if c in payload]
    carried = [c for c in all_cols if c not in payload]
    select_parts = ", ".join(
        ["%s AS " + c for c in edited] + [f"old.{c}" for c in carried]
    )
    insert_cols = ", ".join(edited + carried)
    await cur.execute(
        f"INSERT INTO {table} ({insert_cols}, state, status, version, updated_by) "
        f"SELECT {select_parts}, old.state, 'active', old.version + 1, %s "
        f"FROM {table} old WHERE old.id = %s RETURNING id::text",
        [_adapt(c, payload[c]) for c in edited] + [updated_by, str(row_id)],
    )
    new_id = (await cur.fetchone())[0]
    await repoint_references(cur, table, str(row_id), new_id)
    return new_id


async def set_state(
    cur,
    table: ConfigTable,
    row_id,
    state: Literal["published", "archived"],
    *,
    updated_by: str,
) -> None:
    if state == "archived" and table == _PROTECTED[0]:
        await cur.execute(
            f"SELECT group_slug FROM {table} WHERE id = %s", (str(row_id),)
        )
        row = await cur.fetchone()
        if row is not None and row[0] == _PROTECTED[1]:
            raise ValueError("other profile is protected")
    await cur.execute(
        f"UPDATE {table} SET state = %s, updated_by = %s "
        "WHERE id = %s AND status = 'active' RETURNING id",
        (state, updated_by, str(row_id)),
    )
    if await cur.fetchone() is None:
        raise LookupError(f"{table}: no active row {row_id}")
    if table == "tribute_visual_themes" and state == "published":
        await _repoint_theme_slug_siblings(cur, row_id)


async def _repoint_theme_slug_siblings(cur, new_id) -> None:
    """On theme publish, pull attachments from this slug's OLDER rows onto
    the newly published one.

    Candidate regeneration replaces a same-slug row via
    supersede_active_slug — deliberately without repointing, because the
    replacement is a DRAFT at that moment and repointing would strip a
    live campaign's skin mid-window. The moment the replacement publishes,
    live references must follow or profiles/campaigns keep rendering the
    stale template forever (superseded rows stay readable by id).
    """
    await cur.execute(
        "SELECT slug FROM tribute_visual_themes WHERE id = %s",
        (str(new_id),),
    )
    row = await cur.fetchone()
    if row is None:
        return
    for ref_table in ("relationship_profiles", "tribute_campaigns"):
        await cur.execute(
            f"UPDATE {ref_table} SET visual_theme_id = %s "
            "WHERE status = 'active' AND visual_theme_id IN ("
            "  SELECT id FROM tribute_visual_themes "
            "  WHERE slug = %s AND id != %s)",
            (str(new_id), row[0], str(new_id)),
        )


async def delete_draft_chain(cur, table: ConfigTable, row_id) -> int:
    """Hard-delete a never-published slug chain (CRM junk-draft cleanup).

    Allowed ONLY when ``row_id`` is the slug's ACTIVE row and its state is
    'draft'. Publish/archive flip ``state`` in place and edits carry it
    forward, so an active draft row proves the slug was never published —
    no render snapshot can pin any row in the chain, and the whole chain
    (superseded draft edits included) can be purged, freeing the slug.
    Config references clear via ON DELETE SET NULL. Returns rows deleted.

    Published/archived rows must go through archive (supersession stays
    append-only for anything that could have gone live).
    """
    slug_col = _SLUG_COLUMN[table]
    await cur.execute(
        f"SELECT {slug_col}, state FROM {table} "
        "WHERE id = %s AND status = 'active'",
        (str(row_id),),
    )
    row = await cur.fetchone()
    if row is None:
        raise LookupError(f"{table}: no active row {row_id}")
    slug, state = row
    if state != "draft":
        raise ValueError(
            f"{table}: '{slug}' is {state} — archive it instead of deleting"
        )
    await cur.execute(f"DELETE FROM {table} WHERE {slug_col} = %s", (slug,))
    return cur.rowcount


async def rollback_to(
    cur, table: ConfigTable, old_row_id, *, updated_by: str
) -> str:
    """Republish a prior row's content as a fresh active+published row."""
    slug_col = _SLUG_COLUMN[table]
    await cur.execute(
        f"SELECT {slug_col} FROM {table} WHERE id = %s", (str(old_row_id),)
    )
    row = await cur.fetchone()
    if row is None:
        raise LookupError(f"{table}: no row {old_row_id}")
    slug = row[0]

    # Supersede whatever is currently active for this slug (may be none).
    await cur.execute(
        f"UPDATE {table} SET status = 'superseded', updated_by = %s "
        f"WHERE {slug_col} = %s AND status = 'active' RETURNING id::text",
        (updated_by, slug),
    )
    replaced = [r[0] for r in await cur.fetchall()]
    cols = ", ".join(_COLUMNS[table])
    old_cols = ", ".join(f"old.{c}" for c in _COLUMNS[table])
    await cur.execute(
        f"INSERT INTO {table} ({cols}, state, status, version, updated_by) "
        f"SELECT {old_cols}, 'published', 'active', "
        f"(SELECT max(version) + 1 FROM {table} WHERE {slug_col} = %s), %s "
        f"FROM {table} old WHERE old.id = %s RETURNING id::text",
        (slug, updated_by, str(old_row_id)),
    )
    new_id = (await cur.fetchone())[0]
    for old_id in replaced:
        await repoint_references(cur, table, old_id, new_id)
    return new_id


async def repoint_references(
    cur, table: ConfigTable, old_id: str, new_id: str
) -> None:
    """Follow a supersession: rows referencing the old config row id now
    reference the new one.

    Live config references (profile/campaign -> visual theme, tribute ->
    campaign) point at ROW IDS, and every edit mints a new row — without
    this, editing a theme silently orphans every campaign/profile that
    attached it (they keep rendering the stale version forever). Render
    SNAPSHOTS are untouched: they pin the exact id that was live at
    /generate time, which is the point of snapshots.
    """
    if table == "tribute_visual_themes":
        for ref_table in ("relationship_profiles", "tribute_campaigns"):
            await cur.execute(
                f"UPDATE {ref_table} SET visual_theme_id = %s "
                "WHERE visual_theme_id = %s AND status = 'active'",
                (new_id, old_id),
            )
    elif table == "tribute_campaigns":
        # In-flight tributes follow campaign edits until their /generate
        # snapshot freezes config; completed tributes keep their stamp
        # resolving via the (still readable) superseded row.
        await cur.execute(
            "UPDATE tributes SET campaign_id = %s "
            "WHERE campaign_id = %s AND status IN ('draft', 'ready', 'generating')",
            (new_id, old_id),
        )

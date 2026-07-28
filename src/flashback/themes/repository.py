"""Repository for the ``themes`` table.

Two sync surfaces (extraction worker, thread detector) and two async
surfaces (HTTP endpoints, onboarding/persons flows). Keep both honest by
sharing SQL where possible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg.types.json import Json

from flashback.themes.universal import UNIVERSAL_THEMES


# ---------------------------------------------------------------------------
# Universal seeding
# ---------------------------------------------------------------------------


_SEED_UNIVERSAL_THEME_SQL = """
INSERT INTO themes (person_id, kind, slug, display_name, state)
VALUES (%(person_id)s, 'universal', %(slug)s, %(display_name)s, 'locked')
ON CONFLICT (person_id, slug) WHERE status = 'active' DO NOTHING
"""


async def seed_universal_themes_async(cur, *, person_id: UUID | str) -> None:
    """Seed all five universal themes for a person inside the caller's tx.

    Idempotent: re-running on an already-seeded person is a no-op via
    the partial unique index.
    """
    pid = str(person_id)
    for theme in UNIVERSAL_THEMES:
        await cur.execute(
            _SEED_UNIVERSAL_THEME_SQL,
            {
                "person_id": pid,
                "slug": theme.slug,
                "display_name": theme.display_name,
            },
        )


def seed_universal_themes_sync(cur, *, person_id: UUID | str) -> None:
    """Sync variant (used by extraction worker if needed; mirrors async)."""
    pid = str(person_id)
    for theme in UNIVERSAL_THEMES:
        cur.execute(
            _SEED_UNIVERSAL_THEME_SQL,
            {
                "person_id": pid,
                "slug": theme.slug,
                "display_name": theme.display_name,
            },
        )


# ---------------------------------------------------------------------------
# On-demand tribute theme seeding
# ---------------------------------------------------------------------------


_ENSURE_TRIBUTE_THEME_SQL = """
INSERT INTO themes (person_id, kind, slug, display_name, description, state)
VALUES (%(person_id)s, 'tribute', %(slug)s, %(display_name)s,
        %(description)s, 'locked')
ON CONFLICT (person_id, slug) WHERE status = 'active' DO NOTHING
"""

_SELECT_TRIBUTE_THEME_ID_SQL = """
SELECT id::text FROM active_themes
 WHERE person_id = %(person_id)s AND slug = %(slug)s
 LIMIT 1
"""


async def ensure_tribute_theme_async(
    cur,
    *,
    person_id: UUID | str,
    slug: str,
    display_name: str,
    description: str | None,
) -> str:
    """Ensure the on-demand tribute theme exists; return its id.

    Idempotent via the active-slug partial unique index. Unlike universals,
    the tribute theme is seeded on demand (when the contributor enters the
    flow), not at person creation -- so normal legacies stay clean.
    """
    pid = str(person_id)
    await cur.execute(
        _ENSURE_TRIBUTE_THEME_SQL,
        {
            "person_id": pid,
            "slug": slug,
            "display_name": display_name,
            "description": description,
        },
    )
    await cur.execute(_SELECT_TRIBUTE_THEME_ID_SQL, {"person_id": pid, "slug": slug})
    (theme_id,) = await cur.fetchone()
    return theme_id


# ---------------------------------------------------------------------------
# Theme lookups
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThemeRow:
    id: str
    person_id: str
    kind: str  # 'universal' | 'emergent'
    slug: str
    display_name: str
    description: str | None
    state: str  # 'locked' | 'unlocked'
    archetype_questions: list[dict[str, Any]] | None
    archetype_answers: list[dict[str, Any]] | None
    thread_id: str | None
    archetype_answers_draft: list[dict[str, Any]] | None = None


_SELECT_THEME_COLUMNS = (
    "id::text, person_id::text, kind, slug, display_name, "
    "description, state, archetype_questions, archetype_answers, "
    "thread_id::text, archetype_answers_draft"
)


def _row_to_theme(row) -> ThemeRow:
    (
        tid,
        pid,
        kind,
        slug,
        display_name,
        description,
        state,
        archetype_questions,
        archetype_answers,
        thread_id,
        archetype_answers_draft,
    ) = row
    return ThemeRow(
        id=tid,
        person_id=pid,
        kind=kind,
        slug=slug,
        display_name=display_name,
        description=description,
        state=state,
        archetype_questions=archetype_questions,
        archetype_answers=archetype_answers,
        thread_id=thread_id,
        archetype_answers_draft=archetype_answers_draft,
    )


def fetch_active_themes_for_person_sync(
    cur, *, person_id: str
) -> list[ThemeRow]:
    """Return all active themes for a person.

    Used by the extraction-worker prompt builder to know which emergent
    themes are taggable for this subject (universals are always taggable).
    """
    cur.execute(
        f"""
        SELECT {_SELECT_THEME_COLUMNS}
          FROM active_themes
         WHERE person_id = %s
         ORDER BY kind, slug
        """,
        (person_id,),
    )
    return [_row_to_theme(row) for row in cur.fetchall()]


async def fetch_theme_by_id_async(
    cur, *, theme_id: str, person_id: str | None = None
) -> ThemeRow | None:
    """Fetch a single active theme. If ``person_id`` is given, scope to it."""
    if person_id is not None:
        await cur.execute(
            f"""
            SELECT {_SELECT_THEME_COLUMNS}
              FROM active_themes
             WHERE id = %s AND person_id = %s
            """,
            (theme_id, person_id),
        )
    else:
        await cur.execute(
            f"""
            SELECT {_SELECT_THEME_COLUMNS}
              FROM active_themes
             WHERE id = %s
            """,
            (theme_id,),
        )
    row = await cur.fetchone()
    if row is None:
        return None
    return _row_to_theme(row)


def fetch_theme_by_slug_sync(
    cur, *, person_id: str, slug: str
) -> ThemeRow | None:
    cur.execute(
        f"""
        SELECT {_SELECT_THEME_COLUMNS}
          FROM active_themes
         WHERE person_id = %s AND slug = %s
        """,
        (person_id, slug),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return _row_to_theme(row)


def fetch_theme_slug_to_id_sync(cur, *, person_id: str) -> dict[str, str]:
    """Return a {slug: theme_id} map for all active themes for a person."""
    cur.execute(
        "SELECT slug, id::text FROM active_themes WHERE person_id = %s",
        (person_id,),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


async def update_archetype_questions_async(
    cur, *, theme_id: str, questions: list[dict[str, Any]]
) -> None:
    await cur.execute(
        """
        UPDATE themes
           SET archetype_questions = %s
         WHERE id = %s
           AND status = 'active'
        """,
        (Json(questions), theme_id),
    )


def update_archetype_questions_sync(
    cur, *, theme_id: str, questions: list[dict[str, Any]]
) -> None:
    cur.execute(
        """
        UPDATE themes
           SET archetype_questions = %s
         WHERE id = %s
           AND status = 'active'
        """,
        (Json(questions), theme_id),
    )


async def unlock_theme_async(
    cur, *, theme_id: str, archetype_answers: list[dict[str, Any]]
) -> None:
    """Flip a theme to ``unlocked`` and persist the user's archetype answers.

    Idempotent: re-running for an already-unlocked theme updates the
    answers but keeps the original ``unlocked_at`` via COALESCE.
    """
    await cur.execute(
        """
        UPDATE themes
           SET state                   = 'unlocked',
               archetype_answers       = %s,
               archetype_answers_draft = NULL,
               unlocked_at             = COALESCE(unlocked_at, now())
         WHERE id = %s
           AND status = 'active'
        """,
        (Json(archetype_answers), theme_id),
    )


async def upsert_archetype_draft_async(
    cur, *, theme_id: str, person_id: str, answers: list[dict[str, Any]]
) -> bool:
    """Replace ``archetype_answers_draft`` on a locked theme.

    Returns True if the row was updated, False if no matching active
    locked theme was found. Caller maps False to 404; the 409
    (already unlocked) case is enforced via the state filter.
    """
    await cur.execute(
        """
        UPDATE themes
           SET archetype_answers_draft = %s
         WHERE id = %s
           AND person_id = %s
           AND state = 'locked'
           AND status = 'active'
        """,
        (Json(answers), theme_id, person_id),
    )
    return cur.rowcount > 0


def auto_unlock_rich_themes_sync(cur, *, person_id: str) -> list[dict[str, Any]]:
    """Auto-unlock locked themes that have crossed the ``rich`` threshold.

    Called from the Extraction Worker after ``themed_as`` edges commit.
    Mirrors the ``eligibility = 'rich'`` rule on ``active_themes_with_tier``:
    qualifying_count >= 5 AND life_period_count >= 3 AND has_rich_sensory.

    If a draft exists on the row, it's promoted into ``archetype_answers``
    so the user's mid-flow effort isn't lost. Otherwise
    ``archetype_answers`` stays NULL and the opener grounds on tagged
    moments alone.

    Returns a list of {id, slug, had_draft} dicts for logging.
    """
    cur.execute(
        """
        WITH rich AS (
            SELECT t.id
              FROM themes t
              LEFT JOIN LATERAL (
                  SELECT
                      COUNT(*) FILTER (
                          WHERE m.sensory_details IS NOT NULL
                             OR m.time_anchor IS NOT NULL
                             OR EXISTS (
                                 SELECT 1 FROM edges ie
                                  WHERE ie.from_kind = 'moment'
                                    AND ie.from_id = m.id
                                    AND ie.edge_type = 'involves'
                                    AND ie.status = 'active'
                             )
                      ) AS qualifying_count,
                      COUNT(DISTINCT m.life_period_estimate) FILTER (
                          WHERE m.life_period_estimate IS NOT NULL
                            AND m.life_period_estimate <> ''
                      ) AS life_period_count,
                      bool_or(
                          m.sensory_details IS NOT NULL
                          AND char_length(m.sensory_details) > 80
                      ) AS has_rich_sensory
                    FROM edges e
                    JOIN active_moments m ON m.id = e.from_id
                   WHERE e.from_kind = 'moment'
                     AND e.to_kind   = 'theme'
                     AND e.to_id     = t.id
                     AND e.edge_type = 'themed_as'
                     AND e.status    = 'active'
                     AND m.person_id = t.person_id
              ) stats ON true
             WHERE t.person_id = %s
               AND t.state     = 'locked'
               AND t.status    = 'active'
               AND COALESCE(stats.qualifying_count, 0)  >= 5
               AND COALESCE(stats.life_period_count, 0) >= 3
               AND COALESCE(stats.has_rich_sensory, false)
        )
        UPDATE themes t
           SET state             = 'unlocked',
               unlocked_at       = now(),
               archetype_answers = t.archetype_answers_draft,
               archetype_answers_draft = NULL
          FROM rich
         WHERE t.id = rich.id
        RETURNING t.id::text, t.slug,
                  (t.archetype_answers IS NOT NULL) AS had_draft
        """,
        (person_id,),
    )
    return [
        {"id": row[0], "slug": row[1], "had_draft": bool(row[2])}
        for row in cur.fetchall()
    ]


def insert_emergent_theme_sync(
    cur,
    *,
    person_id: str,
    slug: str,
    display_name: str,
    description: str | None,
    thread_id: str,
    archetype_questions: list[dict[str, Any]] | None,
    generation_prompt: str | None,
) -> str | None:
    """Insert a new emergent theme. Returns the new theme id, or ``None``
    if there's already an active theme with this slug for the person.

    Uses the partial-unique-index conflict to stay idempotent under
    re-runs of the Thread Detector cluster path.
    """
    cur.execute(
        """
        INSERT INTO themes (
            person_id, kind, slug, display_name, description, state,
            archetype_questions, thread_id, generation_prompt
        )
        VALUES (
            %s, 'emergent', %s, %s, %s, 'locked',
            %s, %s, %s
        )
        ON CONFLICT (person_id, slug) WHERE status = 'active' DO NOTHING
        RETURNING id::text
        """,
        (
            person_id,
            slug,
            display_name,
            description,
            Json(archetype_questions) if archetype_questions is not None else None,
            thread_id,
            generation_prompt,
        ),
    )
    row = cur.fetchone()
    return row[0] if row is not None else None

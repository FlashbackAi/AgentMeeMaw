"""Context assembly for the Profile Summary Generator.

Pulls everything the LLM needs for one person:

* Display name + relationship from ``persons``.
* Top traits by strength rank → ``updated_at`` desc, capped per config.
* Top threads by active-moment evidence count, capped per config.
* Top entities by active-moment mention count, capped per config.
* Time period from active-moment ``time_anchor``/``life_period_estimate``
  (delegated to :mod:`time_period`).

Invariants honoured (CLAUDE.md §4):

* #1 (status='active'): all reads scope to ``active_*`` views, with
  active edges.
* #2 (person_id scoping): every query carries ``person_id``;
  cross-legacy bleed is impossible.

The :func:`render_context` helper formats the resulting context into
the user message passed to the LLM. Empty sections are omitted entirely
so the prompt doesn't carry "(none)" filler that the model might
parrot back into the prose.
"""

from __future__ import annotations

from .schema import (
    EntityView,
    ProfileSummaryContext,
    ThreadView,
    TraitView,
)
from .time_period import derive_time_period


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def build_context(
    db_pool,
    *,
    person_id: str,
    top_traits_max: int,
    top_threads_max: int,
    top_entities_max: int,
) -> ProfileSummaryContext:
    """Read everything the summary LLM needs for one person."""
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            person_name, relationship = _fetch_person(cur, person_id=person_id)
            traits = _fetch_top_traits(
                cur, person_id=person_id, limit=top_traits_max
            )
            threads = _fetch_top_threads(
                cur, person_id=person_id, limit=top_threads_max
            )
            entities = _fetch_top_entities(
                cur, person_id=person_id, limit=top_entities_max
            )

    time_period = derive_time_period(db_pool, person_id=person_id)

    return ProfileSummaryContext(
        person_id=person_id,
        person_name=person_name,
        relationship=relationship,
        traits=traits,
        threads=threads,
        entities=entities,
        time_period=time_period,
    )


def render_context(ctx: ProfileSummaryContext) -> str:
    """Format ``ctx`` into the user message passed to the LLM.

    Tagged-section blocks per surface so the model can scan quickly.
    Empty sections are omitted entirely.
    """
    lines: list[str] = []

    lines.append("<subject>")
    lines.append(f"Name: {ctx.person_name}")
    if ctx.relationship:
        lines.append(f"Relationship to contributor: {ctx.relationship}")
    lines.append("</subject>")

    tp_block = _render_time_period(ctx)
    if tp_block:
        lines.append("")
        lines.extend(tp_block)

    if ctx.traits:
        lines.append("")
        lines.append("<traits>")
        for trait in ctx.traits:
            description = (trait.description or "").strip()
            if description:
                lines.append(
                    f"- {trait.name} ({trait.strength}): {description}"
                )
            else:
                lines.append(f"- {trait.name} ({trait.strength})")
        lines.append("</traits>")

    if ctx.threads:
        lines.append("")
        lines.append("<threads>")
        for thread in ctx.threads:
            lines.append(
                f"- {thread.name}: {thread.description} "
                f"(evidenced by {thread.moment_count} moments)"
            )
        lines.append("</threads>")

    if ctx.entities:
        lines.append("")
        lines.append("<entities>")
        for entity in ctx.entities:
            description = (entity.description or "").strip()
            tail = f" — {description}" if description else ""
            lines.append(
                f"- {entity.kind} {entity.name}{tail} "
                f"(mentioned in {entity.mention_count} moments)"
            )
        lines.append("</entities>")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _fetch_person(cur, *, person_id: str) -> tuple[str, str | None]:
    cur.execute(
        "SELECT name, relationship FROM persons WHERE id = %s",
        (person_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"person {person_id!r} not found")
    name, relationship = row
    relationship = None if relationship is None else str(relationship)
    return str(name), relationship


def _fetch_top_traits(cur, *, person_id: str, limit: int) -> list[TraitView]:
    """ORDER BY strength_rank DESC, updated_at DESC, LIMIT ``limit``."""
    cur.execute(
        """
        SELECT name,
               description,
               strength
          FROM active_traits
         WHERE person_id = %s
         ORDER BY CASE strength
                    WHEN 'defining'       THEN 4
                    WHEN 'strong'         THEN 3
                    WHEN 'moderate'       THEN 2
                    WHEN 'mentioned_once' THEN 1
                    ELSE 0
                  END DESC,
                  updated_at DESC
         LIMIT %s
        """,
        (person_id, limit),
    )
    out: list[TraitView] = []
    for name, description, strength in cur.fetchall():
        out.append(
            TraitView(
                name=str(name),
                description=None if description is None else str(description),
                strength=str(strength),  # type: ignore[arg-type]
            )
        )
    return out


def _fetch_top_threads(cur, *, person_id: str, limit: int) -> list[ThreadView]:
    """Top active threads by evidencing-moment count.

    Threads with zero evidencing active moments are filtered out — they
    contribute nothing to the summary and would show up as "(0 moments)"
    noise in the LLM prompt.
    """
    cur.execute(
        """
        SELECT t.name,
               t.description,
               COUNT(DISTINCT m.id) AS moment_count
          FROM active_threads t
          LEFT JOIN active_edges e
                 ON e.from_kind = 'moment'
                AND e.to_kind   = 'thread'
                AND e.to_id     = t.id
                AND e.edge_type = 'evidences'
          LEFT JOIN active_moments m
                 ON m.id = e.from_id
                AND m.person_id = t.person_id
         WHERE t.person_id = %s
         GROUP BY t.id, t.name, t.description, t.created_at
         ORDER BY COUNT(DISTINCT m.id) DESC, t.created_at DESC
         LIMIT %s
        """,
        (person_id, limit),
    )
    out: list[ThreadView] = []
    for name, description, moment_count in cur.fetchall():
        count = int(moment_count or 0)
        if count == 0:
            continue
        out.append(
            ThreadView(
                name=str(name),
                description=str(description),
                moment_count=count,
            )
        )
    return out


def _fetch_top_entities(cur, *, person_id: str, limit: int) -> list[EntityView]:
    """Top active entities by ``involves`` mentions in active moments.

    Entities with zero active-moment mentions are filtered out.
    """
    cur.execute(
        """
        SELECT en.kind,
               en.name,
               en.description,
               COUNT(DISTINCT m.id) AS mention_count
          FROM active_entities en
          LEFT JOIN active_edges e
                 ON e.from_kind = 'moment'
                AND e.to_kind   = 'entity'
                AND e.to_id     = en.id
                AND e.edge_type = 'involves'
          LEFT JOIN active_moments m
                 ON m.id = e.from_id
                AND m.person_id = en.person_id
         WHERE en.person_id = %s
         GROUP BY en.id, en.kind, en.name, en.description, en.created_at
         ORDER BY COUNT(DISTINCT m.id) DESC, en.created_at DESC
         LIMIT %s
        """,
        (person_id, limit),
    )
    out: list[EntityView] = []
    for kind, name, description, mention_count in cur.fetchall():
        count = int(mention_count or 0)
        if count == 0:
            continue
        out.append(
            EntityView(
                kind=str(kind),  # type: ignore[arg-type]
                name=str(name),
                description=None if description is None else str(description),
                mention_count=count,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------


def _render_time_period(ctx: ProfileSummaryContext) -> list[str]:
    """Render the <time_period> block; empty list if nothing to show."""
    tp = ctx.time_period
    if tp.year_range is None and not tp.life_periods:
        return []
    lines = ["<time_period>"]
    if tp.year_range is not None:
        lo, hi = tp.year_range
        if lo == hi:
            lines.append(f"Years: {lo}")
        else:
            lines.append(f"Years: {lo}–{hi}")
    if tp.life_periods:
        lines.append("Life periods: " + ", ".join(tp.life_periods))
    lines.append("</time_period>")
    return lines

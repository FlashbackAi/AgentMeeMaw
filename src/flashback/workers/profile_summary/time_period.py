"""Time period derivation for the Profile Summary Generator.

Code, not LLM. Pulls active moments for the person and computes:

* ``year_range``: ``(min, max)`` of non-null ``time_anchor.year`` values,
  or ``None`` if no active moments have a year anchor.
* ``life_periods``: distinct ``life_period_estimate`` strings, ordered
  by approximate chronology (see :data:`LIFE_PERIOD_ORDER`). Unknown
  strings sort to the end alphabetically.

The chronology mapping is best-effort; we'll refine it as we observe
what the LLM actually emits during extraction.
"""

from __future__ import annotations

from .schema import TimePeriodView


# Approximate life-period chronology — best-effort ordering for display.
# Matches the values we expect the Extraction Worker to assign as
# ``moments.life_period_estimate``. Unknown values sort to the end
# alphabetically (see :func:`order_life_periods`).
LIFE_PERIOD_ORDER: tuple[str, ...] = (
    "childhood",
    "youth",
    "young adult",
    "early career",
    "career",
    "parenthood",
    "midlife",
    "later years",
    "retirement",
    "late life",
)


def order_life_periods(periods: set[str]) -> list[str]:
    """Order known life-period strings by approximate chronology.

    Unknown strings sort to the end alphabetically.
    """
    known = [p for p in LIFE_PERIOD_ORDER if p in periods]
    unknown = sorted(p for p in periods if p not in LIFE_PERIOD_ORDER)
    return known + unknown


def derive_time_period(db_pool, *, person_id: str) -> TimePeriodView:
    """Compute year range and/or life_period set from active moments.

    Year range: earliest and latest non-null ``time_anchor->>'year'``
    across the person's active moments.

    Life periods: distinct non-null ``life_period_estimate`` values,
    ordered by :data:`LIFE_PERIOD_ORDER`.

    Honors invariants #1 (active-only) and #2 (person scoping).
    """
    with db_pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT (time_anchor->>'year')::int AS year,
                       life_period_estimate
                  FROM active_moments
                 WHERE person_id = %s
                """,
                (person_id,),
            )
            rows = cur.fetchall()

    years = [int(year) for year, _ in rows if year is not None]
    year_range = (min(years), max(years)) if years else None

    life_periods_raw = {
        str(lp) for _, lp in rows if lp is not None and str(lp) != ""
    }
    life_periods = order_life_periods(life_periods_raw)

    return TimePeriodView(year_range=year_range, life_periods=life_periods)

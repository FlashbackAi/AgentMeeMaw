"""Tests for time-period derivation (DB-touching)."""

from __future__ import annotations

from flashback.workers.profile_summary.time_period import (
    LIFE_PERIOD_ORDER,
    derive_time_period,
    order_life_periods,
)

from tests.workers.profile_summary.fixtures.sample_profiles import seed_moment


# ---------------------------------------------------------------------------
# order_life_periods (pure function, no DB)
# ---------------------------------------------------------------------------


def test_order_life_periods_known_in_order():
    out = order_life_periods({"retirement", "childhood", "career"})
    assert out == ["childhood", "career", "retirement"]


def test_order_life_periods_unknown_sorts_alphabetically_after_known():
    out = order_life_periods({"flux", "career", "alpha", "childhood"})
    # Known first (in chronology); unknown alphabetical after.
    assert out == ["childhood", "career", "alpha", "flux"]


def test_order_life_periods_empty():
    assert order_life_periods(set()) == []


def test_life_period_order_chronology_intact():
    """Drift detector for the ordered tuple itself."""
    assert LIFE_PERIOD_ORDER[0] == "childhood"
    assert LIFE_PERIOD_ORDER[-1] == "late life"
    # Stays sorted by intuitive chronology.
    assert LIFE_PERIOD_ORDER.index("youth") < LIFE_PERIOD_ORDER.index("midlife")
    assert LIFE_PERIOD_ORDER.index("parenthood") < LIFE_PERIOD_ORDER.index("retirement")


# ---------------------------------------------------------------------------
# derive_time_period (DB-touching)
# ---------------------------------------------------------------------------


def test_derive_time_period_all_years(db_pool, make_person):
    person_id = make_person("Years")
    seed_moment(db_pool, person_id=person_id, time_anchor={"year": 1962})
    seed_moment(db_pool, person_id=person_id, time_anchor={"year": 1985})
    seed_moment(db_pool, person_id=person_id, time_anchor={"year": 2003})

    tp = derive_time_period(db_pool, person_id=person_id)
    assert tp.year_range == (1962, 2003)
    assert tp.life_periods == []


def test_derive_time_period_no_years_only_life_periods(db_pool, make_person):
    person_id = make_person("LifePeriods")
    seed_moment(db_pool, person_id=person_id, life_period_estimate="retirement")
    seed_moment(db_pool, person_id=person_id, life_period_estimate="childhood")
    seed_moment(db_pool, person_id=person_id, life_period_estimate="early career")

    tp = derive_time_period(db_pool, person_id=person_id)
    assert tp.year_range is None
    # Sorted by chronology.
    assert tp.life_periods == ["childhood", "early career", "retirement"]


def test_derive_time_period_mixed_years_and_periods(db_pool, make_person):
    person_id = make_person("Mixed")
    seed_moment(
        db_pool,
        person_id=person_id,
        time_anchor={"year": 1942},
        life_period_estimate="childhood",
    )
    seed_moment(
        db_pool,
        person_id=person_id,
        time_anchor={"year": 2019},
        life_period_estimate="late life",
    )
    # A moment with a life period but no year still contributes.
    seed_moment(
        db_pool,
        person_id=person_id,
        life_period_estimate="midlife",
    )

    tp = derive_time_period(db_pool, person_id=person_id)
    assert tp.year_range == (1942, 2019)
    assert tp.life_periods == ["childhood", "midlife", "late life"]


def test_derive_time_period_unknown_life_period_sorts_to_end(db_pool, make_person):
    person_id = make_person("UnknownLP")
    seed_moment(db_pool, person_id=person_id, life_period_estimate="zebra")
    seed_moment(db_pool, person_id=person_id, life_period_estimate="aardvark")
    seed_moment(db_pool, person_id=person_id, life_period_estimate="childhood")

    tp = derive_time_period(db_pool, person_id=person_id)
    assert tp.year_range is None
    # "childhood" is known; the other two are unknown, alphabetical.
    assert tp.life_periods == ["childhood", "aardvark", "zebra"]


def test_derive_time_period_zero_moments(db_pool, make_person):
    person_id = make_person("Empty")
    tp = derive_time_period(db_pool, person_id=person_id)
    assert tp.year_range is None
    assert tp.life_periods == []


def test_derive_time_period_skips_superseded_moments(db_pool, make_person):
    person_id = make_person("Super")
    seed_moment(
        db_pool,
        person_id=person_id,
        time_anchor={"year": 1950},
        status="superseded",
    )
    seed_moment(
        db_pool,
        person_id=person_id,
        time_anchor={"year": 1980},
    )
    tp = derive_time_period(db_pool, person_id=person_id)
    # The superseded moment with year 1950 must NOT be counted.
    assert tp.year_range == (1980, 1980)

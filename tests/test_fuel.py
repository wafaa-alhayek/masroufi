"""What a dish costs in cooking gas."""

import pytest

from app.config import settings
from app.kitchen.fuel import SOAKING_SAVING, cylinder_days, estimate


def test_no_cooking_costs_no_gas():
    """Bread with zaatar is in the library precisely for a week with no gas."""
    assert estimate(0, 0, 0).kg == 0.0


def test_full_flame_matches_the_published_burner_rate():
    # One burner, one hour, at 0.25 kg/h.
    assert estimate(60, 0, 1).kg == pytest.approx(settings.burner_kg_per_hour)


def test_a_simmer_costs_less_than_full_flame():
    assert estimate(0, 60, 1).kg < estimate(60, 0, 1).kg


def test_two_burners_cost_twice_as_much():
    assert estimate(20, 40, 2).kg == pytest.approx(estimate(20, 40, 1).kg * 2)


def test_beans_cost_more_to_cook_than_a_quick_dish():
    """The case that justifies the feature: a long simmer is the expensive part."""
    beans = estimate(10, 75, 1)
    eggs = estimate(10, 0, 1)
    assert beans.kg > eggs.kg * 2


def test_soaking_saving_is_offered_only_for_pulses():
    assert estimate(10, 75, 1, soakable=True).soaking_saves_kg is not None
    assert estimate(10, 75, 1, soakable=False).soaking_saves_kg is None


def test_soaking_saves_the_documented_fraction_of_the_simmer():
    gas = estimate(10, 75, 1, soakable=True)
    simmer_only = estimate(0, 75, 1).kg
    assert gas.soaking_saves_kg == pytest.approx(simmer_only * SOAKING_SAVING, rel=1e-3)


def test_nothing_to_save_when_there_is_no_simmer():
    assert estimate(10, 0, 1, soakable=True).soaking_saves_kg is None


def test_cost_is_reported_when_a_price_is_known():
    gas = estimate(60, 0, 1, price_per_kg=20.0)
    assert gas.cost == pytest.approx(settings.burner_kg_per_hour * 20.0, rel=1e-3)


def test_no_cost_without_a_price():
    assert estimate(60, 0, 1).cost is None


def test_minutes_is_the_total_time():
    assert estimate(12, 35, 1).minutes == 47


def test_daily_burn_matches_the_published_household_figure():
    """A family cooking three meals a day is reported near 0.3 kg/day.

    Three dishes of roughly this shape should land in that order of magnitude;
    if this drifts far, the burner rates need revisiting.
    """
    day = estimate(12, 35, 1).kg + estimate(5, 20, 1).kg + estimate(10, 30, 1).kg
    assert 0.15 < day < 0.6


def test_cylinder_days_divides_available_gas_by_the_burn_rate():
    assert cylinder_days(12.0, 0.3) == 40.0


def test_cylinder_days_is_undefined_when_nothing_is_being_cooked():
    assert cylinder_days(12.0, 0.0) is None

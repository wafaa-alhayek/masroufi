"""The Q10 shelf-life model, and the weather source behind it."""

import asyncio
from datetime import date

import httpx
import pytest

from app.kitchen.spoilage import (
    DAILY_THRESHOLD_DAYS,
    MAX_MODELLED_C,
    MIN_MODELLED_C,
    REFERENCE_C,
    keeps_for,
    storage_note,
)
from app.weather import (
    CLIMATE_NORMALS_C,
    ClimateNormalTemperatures,
    OpenMeteoTemperatures,
)

# Bread: 3 days at the 20 C reference, Q10 of 2.5.
BREAD = (3.0, 2.5)


def test_reference_temperature_returns_the_reference_life():
    assert keeps_for(*BREAD, REFERENCE_C).days == 3.0


def test_q10_halves_life_per_ten_degrees_when_q10_is_two():
    assert keeps_for(10.0, 2.0, REFERENCE_C).days == 10.0
    assert keeps_for(10.0, 2.0, REFERENCE_C + 10).days == 5.0
    assert keeps_for(10.0, 2.0, REFERENCE_C + 20).days == 2.5


def test_cold_extends_shelf_life():
    assert keeps_for(10.0, 2.0, REFERENCE_C - 10).days == 20.0


def test_the_winter_summer_case_the_feature_exists_for():
    """Bread can be bought ahead in January and cannot in August."""
    january = keeps_for(*BREAD, CLIMATE_NORMALS_C[1])
    august = keeps_for(*BREAD, CLIMATE_NORMALS_C[8])

    assert not january.buy_daily, "17 C: buying tomorrow's bread today is reasonable"
    assert august.buy_daily, "31 C: the same bread will not last"
    assert august.days < january.days / 2


def test_heat_shortening_is_flagged():
    assert keeps_for(*BREAD, 32).shortened
    assert not keeps_for(*BREAD, REFERENCE_C).shortened


def test_stable_goods_barely_move():
    """Rice has a low Q10, so a hot week does not make it a daily purchase."""
    assert not keeps_for(540, 1.6, 35).buy_daily
    assert keeps_for(540, 1.6, 35).days > 100


def test_meat_never_becomes_a_weekly_buy_at_any_ambient_temperature():
    for temperature in range(10, 46, 5):
        assert keeps_for(0.4, 3.5, temperature).buy_daily


def test_the_model_floor_refuses_to_assume_a_fridge():
    """Extrapolating to freezing would say raw chicken keeps five days.

    True in a fridge, and exactly the assumption this app does not make, so the
    curve is clamped to ambient temperatures.
    """
    assert keeps_for(0.4, 3.5, -5).at_c == MIN_MODELLED_C
    assert keeps_for(0.4, 3.5, -5).buy_daily


def test_absurd_temperatures_are_clamped():
    assert keeps_for(*BREAD, 500).at_c == MAX_MODELLED_C
    assert keeps_for(*BREAD, -100).at_c == MIN_MODELLED_C


def test_zero_shelf_life_is_always_daily():
    assert keeps_for(0, 2.0, 15).buy_daily


def test_threshold_is_the_documented_boundary():
    assert keeps_for(DAILY_THRESHOLD_DAYS + 0.1, 1.0, REFERENCE_C).buy_daily is False
    assert keeps_for(DAILY_THRESHOLD_DAYS - 0.1, 1.0, REFERENCE_C).buy_daily is True


# --- advice ------------------------------------------------------------------


def test_advice_names_both_numbers_when_heat_shortens_life():
    note = storage_note("Bread", keeps_for(*BREAD, 32))
    assert "32" in note and "3" in note
    assert "day" in note


def test_advice_is_quiet_about_things_that_keep():
    assert storage_note("Rice", keeps_for(540, 1.6, 35)) is None


def test_advice_for_something_that_never_keeps():
    note = storage_note("Chicken", keeps_for(0.4, 3.5, 20))
    assert "does not keep" in note


def test_advice_suggests_using_it_early_when_merely_shortened():
    note = storage_note("Potatoes", keeps_for(21, 2.0, 34))
    assert note is not None
    assert "early in the week" in note


# --- weather sources ---------------------------------------------------------


def test_climate_normals_cover_every_month():
    assert set(CLIMATE_NORMALS_C) == set(range(1, 13))
    assert max(CLIMATE_NORMALS_C.values()) > min(CLIMATE_NORMALS_C.values()) + 8


def test_offline_source_returns_the_seasonal_average():
    out = asyncio.run(
        ClimateNormalTemperatures().daily_max(date(2026, 8, 1), date(2026, 8, 3))
    )
    assert len(out) == 3
    assert all(r.estimated for r in out.values())
    assert out[date(2026, 8, 1)].max_c == CLIMATE_NORMALS_C[8]


def _open_meteo(handler) -> OpenMeteoTemperatures:
    source = OpenMeteoTemperatures()
    source._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.open-meteo.com"
    )
    return source


def test_open_meteo_parses_its_response():
    payload = {
        "daily": {
            "time": ["2026-08-01", "2026-08-02"],
            "temperature_2m_max": [33.4, 31.9],
        }
    }

    async def run():
        source = _open_meteo(lambda r: httpx.Response(200, json=payload))
        try:
            return await source.daily_max(date(2026, 8, 1), date(2026, 8, 2))
        finally:
            await source.aclose()

    out = asyncio.run(run())
    assert out[date(2026, 8, 1)].max_c == 33.4
    assert not out[date(2026, 8, 1)].estimated


def test_a_weather_outage_falls_back_to_normals_and_says_so():
    """A shopping list must never fail because the weather service is down."""

    async def run():
        source = _open_meteo(lambda r: httpx.Response(500))
        try:
            return await source.daily_max(date(2026, 8, 1), date(2026, 8, 1))
        finally:
            await source.aclose()

    out = asyncio.run(run())
    assert out[date(2026, 8, 1)].max_c == CLIMATE_NORMALS_C[8]
    assert out[date(2026, 8, 1)].estimated, "the caller must be able to say it is a guess"


def test_missing_days_in_the_response_fall_back_individually():
    payload = {"daily": {"time": ["2026-08-01"], "temperature_2m_max": [33.4]}}

    async def run():
        source = _open_meteo(lambda r: httpx.Response(200, json=payload))
        try:
            return await source.daily_max(date(2026, 8, 1), date(2026, 8, 2))
        finally:
            await source.aclose()

    out = asyncio.run(run())
    assert not out[date(2026, 8, 1)].estimated
    assert out[date(2026, 8, 2)].estimated


def test_repeat_requests_are_served_from_cache():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(
            200,
            json={"daily": {"time": ["2026-08-01"], "temperature_2m_max": [33.4]}},
        )

    async def run():
        source = _open_meteo(handler)
        try:
            await source.daily_max(date(2026, 8, 1), date(2026, 8, 1))
            await source.daily_max(date(2026, 8, 1), date(2026, 8, 1))
        finally:
            await source.aclose()

    asyncio.run(run())
    assert calls["n"] == 1

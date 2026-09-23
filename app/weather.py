"""Daily temperature, for working out how long food will actually keep.

Open-Meteo is used because it needs no API key and no account, which is one less
credential for a household app to manage, and it serves forecast and recent
history from the same call.

Connectivity is not assumed. When the network is unavailable the climate normals
below are used instead, so the shelf-life maths still runs — a seasonal average
is a far better guess than pretending every day is 20 degrees.
"""

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

import httpx

from app.config import settings

# Gaza City. Overridable in settings for anywhere else.
DEFAULT_LATITUDE = 31.5017
DEFAULT_LONGITUDE = 34.4668

# Mean daily maximum by month, in Celsius, for the Gaza coast. Approximate
# climate normals used only when the network is unavailable.
CLIMATE_NORMALS_C: dict[int, float] = {
    1: 17.5,
    2: 18.0,
    3: 20.0,
    4: 23.5,
    5: 26.0,
    6: 28.5,
    7: 30.0,
    8: 31.0,
    9: 30.0,
    10: 28.0,
    11: 23.5,
    12: 19.5,
}


@dataclass(frozen=True)
class DayTemperature:
    on: date
    max_c: float
    estimated: bool


class TemperatureSource(Protocol):
    async def daily_max(self, start: date, end: date) -> dict[date, DayTemperature]: ...

    async def aclose(self) -> None: ...


def _normal_for(day: date) -> DayTemperature:
    return DayTemperature(on=day, max_c=CLIMATE_NORMALS_C[day.month], estimated=True)


def _span(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


class ClimateNormalTemperatures:
    """Offline source: the seasonal average for the month."""

    async def daily_max(self, start: date, end: date) -> dict[date, DayTemperature]:
        return {day: _normal_for(day) for day in _span(start, end)}

    async def aclose(self) -> None:
        return None


class OpenMeteoTemperatures:
    """Live source, falling back to climate normals for any day it cannot supply."""

    def __init__(self, latitude: float | None = None, longitude: float | None = None) -> None:
        self._latitude = latitude if latitude is not None else settings.latitude
        self._longitude = longitude if longitude is not None else settings.longitude
        self._client = httpx.AsyncClient(
            base_url=settings.open_meteo_base_url,
            timeout=httpx.Timeout(15.0, connect=8.0),
        )
        self._cache: dict[date, DayTemperature] = {}

    async def daily_max(self, start: date, end: date) -> dict[date, DayTemperature]:
        days = _span(start, end)
        if all(day in self._cache for day in days):
            return {day: self._cache[day] for day in days}

        fetched: dict[date, float] = {}
        try:
            fetched = await self._fetch(start, end)
        except Exception:
            # A weather outage must never fail a shopping list. Fall through to
            # the normals, flagged as estimated so the UI can say so.
            fetched = {}

        out: dict[date, DayTemperature] = {}
        for day in days:
            if day in fetched:
                out[day] = DayTemperature(on=day, max_c=fetched[day], estimated=False)
            else:
                out[day] = self._cache.get(day) or _normal_for(day)
            self._cache[day] = out[day]
        return out

    async def _fetch(self, start: date, end: date, attempts: int = 2) -> dict[date, float]:
        params = {
            "latitude": self._latitude,
            "longitude": self._longitude,
            "daily": "temperature_2m_max",
            "timezone": "auto",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                resp = await self._client.get("/v1/forecast", params=params)
                resp.raise_for_status()
                daily = resp.json().get("daily") or {}
                times = daily.get("time") or []
                maxima = daily.get("temperature_2m_max") or []
                return {
                    date.fromisoformat(day): float(value)
                    for day, value in zip(times, maxima)
                    if value is not None
                }
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                last = exc
                if attempt < attempts - 1:
                    await asyncio.sleep(1)
        raise RuntimeError("Open-Meteo request failed") from last

    async def aclose(self) -> None:
        await self._client.aclose()


def build_temperature_source() -> TemperatureSource:
    if settings.weather_enabled:
        return OpenMeteoTemperatures()
    return ClimateNormalTemperatures()

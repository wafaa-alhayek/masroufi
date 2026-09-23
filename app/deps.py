"""Process-wide singletons, so their caches are shared across requests."""

from app.classify import Classifier, build_classifier
from app.notes import NoteCleaner
from app.translate import build_translator
from app.weather import TemperatureSource, build_temperature_source

_classifier: Classifier | None = None
_cleaner: NoteCleaner | None = None
_temperatures: TemperatureSource | None = None


def get_classifier() -> Classifier:
    global _classifier
    if _classifier is None:
        _classifier = build_classifier()
    return _classifier


def get_cleaner() -> NoteCleaner:
    global _cleaner
    if _cleaner is None:
        _cleaner = NoteCleaner(build_translator())
    return _cleaner


async def close_all() -> None:
    global _classifier, _cleaner, _temperatures
    if _classifier is not None:
        await _classifier.aclose()
        _classifier = None
    if _cleaner is not None:
        await _cleaner.aclose()
        _cleaner = None
    if _temperatures is not None:
        await _temperatures.aclose()
        _temperatures = None


def get_temperature_source() -> TemperatureSource:
    """One source per process, so its day cache is shared across requests."""
    global _temperatures
    if _temperatures is None:
        _temperatures = build_temperature_source()
    return _temperatures

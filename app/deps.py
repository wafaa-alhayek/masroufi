"""Process-wide singletons, so their caches are shared across requests."""

from app.classify import Classifier, build_classifier
from app.notes import NoteCleaner
from app.translate import build_translator

_classifier: Classifier | None = None
_cleaner: NoteCleaner | None = None


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
    global _classifier, _cleaner
    if _classifier is not None:
        await _classifier.aclose()
        _classifier = None
    if _cleaner is not None:
        await _cleaner.aclose()
        _cleaner = None

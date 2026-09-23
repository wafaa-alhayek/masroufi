"""One classifier for the process, so its cache is shared across requests."""

from app.classify import Classifier, build_classifier

_classifier: Classifier | None = None


def get_classifier() -> Classifier:
    global _classifier
    if _classifier is None:
        _classifier = build_classifier()
    return _classifier


async def close_classifier() -> None:
    global _classifier
    if _classifier is not None:
        await _classifier.aclose()
        _classifier = None

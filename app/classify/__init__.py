from app.config import settings

from .base import Classifier, Decision
from .cache import CachingClassifier
from .mock import MockClassifier

__all__ = ["Classifier", "Decision", "build_classifier"]


def build_classifier() -> Classifier:
    """Pick a backend from config and wrap it in the decision cache.

    Note cleaning and translation happen before this, in NoteCleaner, so that
    the cleaned vendor name is available to the vendor table as well as to the
    classifier.
    """
    backend = settings.classifier_backend.lower()
    if backend == "jev":
        from .jev import JevClassifier

        inner: Classifier = JevClassifier()
    elif backend == "mock":
        inner = MockClassifier()
    else:
        raise ValueError(f"Unknown CLASSIFIER_BACKEND: {settings.classifier_backend!r}")

    return CachingClassifier(inner)

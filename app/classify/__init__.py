from app.config import settings

from .base import Classifier, Decision
from .cache import CachingClassifier
from .mock import MockClassifier
from .translating import TranslatingClassifier

__all__ = ["Classifier", "Decision", "build_classifier"]


def build_classifier() -> Classifier:
    """Assemble the chain: cache → translate → decide.

    The cache sits outermost and is keyed on the original note, so a repeat
    vendor costs neither a translation nor a classification after the first time.
    """
    backend = settings.classifier_backend.lower()
    if backend == "jev":
        from .jev import JevClassifier

        inner: Classifier = JevClassifier()
    elif backend == "mock":
        inner = MockClassifier()
    else:
        raise ValueError(f"Unknown CLASSIFIER_BACKEND: {settings.classifier_backend!r}")

    if settings.translate_notes:
        from app.translate import build_translator

        inner = TranslatingClassifier(inner, build_translator())

    return CachingClassifier(inner)

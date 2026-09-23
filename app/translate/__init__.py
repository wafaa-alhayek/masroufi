from app.config import settings

from .base import NoopTranslator, Translator

__all__ = ["Translator", "NoopTranslator", "build_translator"]


def build_translator() -> Translator:
    if not settings.translate_notes:
        return NoopTranslator()

    from .openrouter import OpenRouterTranslator

    return OpenRouterTranslator()

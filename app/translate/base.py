"""Note translation, as a step in front of the classifier.

Bank notes arrive in Arabic, in transliterated Arabic, or mixed with English.
Rather than depend on the decision model handling all of that, notes can be
normalised to English first.

This is a boundary, not an implementation: if Jev turns out to handle Arabic
well, the no-op translator keeps the pipeline identical and one call shorter.
"""

from typing import Protocol


class Translator(Protocol):
    async def to_english(self, text: str) -> str: ...

    async def aclose(self) -> None: ...


class NoopTranslator:
    """Pass the note through untouched."""

    async def to_english(self, text: str) -> str:
        return text

    async def aclose(self) -> None:
        return None

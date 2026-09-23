"""The classifier boundary.

Everything the app knows about categorising a note goes through Classifier. Jev
is one implementation; the mock is another. If Jev turns out to handle Arabic
notes badly, only this package changes.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Decision:
    category: str
    confidence: float
    probabilities: dict[str, float]
    is_merchant: float | None = None
    reducibility: int | None = None

    def needs_review(self, threshold: float) -> bool:
        return self.confidence < threshold


class Classifier(Protocol):
    async def classify(self, note: str, amount: float, currency: str) -> Decision: ...

    async def aclose(self) -> None: ...

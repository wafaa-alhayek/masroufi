from datetime import date, datetime, timezone
from enum import Enum

from sqlmodel import Field, SQLModel


class Currency(str, Enum):
    ILS = "ILS"
    USD = "USD"
    JOD = "JOD"
    EGP = "EGP"


class CategorySource(str, Enum):
    """Where a transaction's current category came from.

    USER always wins over MODEL: a correction is never overwritten by a later
    re-classification.
    """

    MODEL = "model"
    USER = "user"
    RULE = "rule"
    UNSET = "unset"


class Transaction(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)

    booked_on: date
    amount: float = Field(description="Positive for money in, negative for money out.")
    currency: Currency = Currency.ILS

    note: str = Field(description="As it appeared on the statement, unmodified.")
    note_key: str = Field(index=True, description="Normalised note, for caching and grouping.")

    category: str | None = Field(default=None, index=True)
    category_source: CategorySource = CategorySource.UNSET
    confidence: float | None = None
    reducibility: int | None = Field(
        default=None, description="Index into taxonomy.REDUCIBILITY_SCALE."
    )
    needs_review: bool = Field(default=True, index=True)

    import_batch: str = Field(index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RecurringCandidate(SQLModel, table=True):
    """A detected repeat pattern, awaiting the user's confirmation.

    Found by arithmetic, not by the model: same normalised note or same amount,
    recurring on a roughly regular cadence.
    """

    id: int | None = Field(default=None, primary_key=True)

    note_key: str = Field(index=True)
    amount: float
    currency: Currency = Currency.ILS
    occurrences: int
    median_gap_days: float
    first_seen: date
    last_seen: date

    suggested_category: str | None = None
    suggested_label: str | None = None
    confirmed: bool | None = Field(
        default=None, description="None = not yet answered, True/False = user's answer."
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

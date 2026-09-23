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


class Vendor(SQLModel, table=True):
    """A place the household pays, accumulated across imports.

    This is the dataset the tidying layer exists to build. Once a vendor's
    category is confirmed by the household, later transactions from the same
    vendor are categorised from this table with no model call at all — the app
    gets cheaper and more accurate the longer it is used.
    """

    id: int | None = Field(default=None, primary_key=True)

    name: str = Field(index=True, description="Tidied display name.")
    match_key: str = Field(index=True, unique=True, description="What aliases resolve to.")

    category: str | None = Field(default=None, index=True)
    category_confirmed: bool = Field(
        default=False, description="True once a human said so. Never overwritten by a model."
    )

    times_seen: int = Field(default=0)
    total_spent: float = Field(default=0.0)
    first_seen: date | None = None
    last_seen: date | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class VendorAlias(SQLModel, table=True):
    """Every spelling of a vendor that has been seen on a statement.

    Kept so that the same shop written three ways stays one vendor, and so a bad
    merge can be traced back to the note that caused it.
    """

    id: int | None = Field(default=None, primary_key=True)
    vendor_id: int = Field(foreign_key="vendor.id", index=True)
    alias_key: str = Field(index=True, unique=True)
    raw_example: str = Field(description="One real note that produced this alias.")


class Transaction(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)

    booked_on: date
    amount: float = Field(description="Positive for money in, negative for money out.")
    currency: Currency = Currency.ILS

    note: str = Field(description="As it appeared on the statement, unmodified.")
    note_key: str = Field(index=True, description="Normalised note, for caching and grouping.")
    vendor_id: int | None = Field(default=None, foreign_key="vendor.id", index=True)

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

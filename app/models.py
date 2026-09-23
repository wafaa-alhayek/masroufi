from datetime import date, datetime, timezone
from enum import Enum

from sqlmodel import Field, SQLModel


class Currency(str, Enum):
    ILS = "ILS"
    USD = "USD"
    JOD = "JOD"
    EGP = "EGP"


class SourceKind(str, Enum):
    BANK = "bank"
    WALLET = "wallet"
    CASH = "cash"
    MANUAL = "manual"


class Source(SQLModel, table=True):
    """Where transactions came from.

    Households in Gaza pay through several providers at once — a bank plus two or
    three wallets — and no single export shows the whole picture. Every
    transaction therefore belongs to a source, so that imports can be deduplicated
    per provider and money moved between the household's own accounts can be
    recognised instead of counted as spending.
    """

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)
    name: str
    kind: SourceKind = SourceKind.WALLET
    default_currency: Currency = Currency.ILS


class CategorySource(str, Enum):
    """Where a transaction's current category came from.

    USER always wins over MODEL: a correction is never overwritten by a later
    re-classification.
    """

    MODEL = "model"
    USER = "user"
    RULE = "rule"
    UNSET = "unset"


class Household(SQLModel, table=True):
    """Who eats, so quantities can be scaled to them.

    Two numbers rather than a profile per person: it is enough to stop the app
    over-buying, and it stores less about a family than the alternative.
    """

    id: int | None = Field(default=None, primary_key=True)
    adults: int = Field(default=2, ge=0)
    children: int = Field(default=0, ge=0)
    child_portion_ratio: float = Field(
        default=0.6,
        gt=0,
        le=1,
        description="A child's share of an adult portion. Adjustable, because a "
        "teenager eats like an adult.",
    )

    @property
    def adult_equivalents(self) -> float:
        return self.adults + self.children * self.child_portion_ratio


class ShelfLife(str, Enum):
    """How long something keeps without refrigeration.

    This drives the whole shopping-list shape: refrigeration is scarce, so
    perishables are bought the day they are cooked, while anything that keeps is
    aggregated into one weekly shop for the better unit price.
    """

    PERISHABLE = "perishable"
    KEEPS_DAYS = "keeps_days"
    STABLE = "stable"

    @property
    def buy_daily(self) -> bool:
        return self is ShelfLife.PERISHABLE


class ItemRole(str, Enum):
    """What an ingredient does in a dish, so a substitute can be suggested when
    it is not available at the shop."""

    GRAIN = "grain"
    PULSE = "pulse"
    MEAT = "meat"
    POULTRY = "poultry"
    FISH = "fish"
    EGG = "egg"
    DAIRY = "dairy"
    VEG_LEAF = "veg_leaf"
    VEG_FRUIT = "veg_fruit"
    VEG_ROOT = "veg_root"
    FRUIT = "fruit"
    BREAD = "bread"
    FAT = "fat"
    SWEETENER = "sweetener"
    SPICE = "spice"
    PASTE = "paste"
    DRINK = "drink"
    OTHER = "other"


class Unit(str, Enum):
    GRAM = "g"
    ML = "ml"
    PIECE = "piece"
    BUNCH = "bunch"


class Item(SQLModel, table=True):
    """A grocery item the household might buy."""

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)
    name_en: str
    name_ar: str
    unit: Unit = Unit.GRAM
    shelf_life: ShelfLife = ShelfLife.STABLE
    role: ItemRole = ItemRole.OTHER
    purchase_step: float = Field(
        default=1.0,
        gt=0,
        description="Smallest sensible amount to buy, e.g. 50 g of rice or 1 egg. "
        "Quantities are rounded up to a multiple of this after aggregating, not "
        "before, so rounding does not multiply waste across a week.",
    )
    keeps_days_at_20c: float = Field(
        default=180.0,
        gt=0,
        description="Shelf life at the 20 degree reference, without refrigeration.",
    )
    q10: float = Field(
        default=2.0,
        gt=1,
        description="How much faster this spoils per 10 degrees. About 2 for most "
        "foods, higher for fresh produce and meat where microbial growth drives it.",
    )
    needs_review: bool = Field(
        default=False,
        description="Created automatically rather than from the seeded catalogue, "
        "so its unit, role and shelf life are a guess until someone checks.",
    )


class DishSource(str, Enum):
    SEED = "seed"
    AI = "ai"
    USER = "user"


class MealSlot(str, Enum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"


class Dish(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)
    name_en: str
    name_ar: str
    default_slot: MealSlot = MealSlot.LUNCH

    # Cooking fuel. Where gas is scarce, a long simmer is part of a dish's price,
    # so a plan can be checked against the gas actually available.
    full_flame_minutes: int = Field(default=0, ge=0)
    simmer_minutes: int = Field(default=0, ge=0)
    burners: int = Field(default=1, ge=0, description="Rings lit at once.")

    source: DishSource = DishSource.SEED
    edited_by_household: bool = Field(default=False)
    needs_review: bool = Field(
        default=False, description="True for AI-generated dishes until a person checks them."
    )


class DishItem(SQLModel, table=True):
    """An ingredient of a dish, quantified per adult portion."""

    id: int | None = Field(default=None, primary_key=True)
    dish_id: int = Field(foreign_key="dish.id", index=True)
    item_id: int = Field(foreign_key="item.id", index=True)
    qty_per_adult: float = Field(gt=0)
    optional: bool = Field(default=False)


class DishPortion(SQLModel, table=True):
    """The household's remembered 'we need more/less than this for that dish'."""

    id: int | None = Field(default=None, primary_key=True)
    dish_id: int = Field(foreign_key="dish.id", index=True, unique=True)
    factor: float = Field(default=1.0, gt=0)


class PlannedMeal(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    plan_date: date = Field(index=True)
    slot: MealSlot = MealSlot.LUNCH
    dish_id: int = Field(foreign_key="dish.id", index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GasBudget(SQLModel, table=True):
    """How much cooking gas the household has for a period, and what it cost.

    Entered by the household because nothing else can know it: a cylinder's
    remaining weight is not on any statement.
    """

    id: int | None = Field(default=None, primary_key=True)
    starts_on: date = Field(index=True)
    ends_on: date
    kg_available: float = Field(gt=0)
    price_per_kg: float | None = Field(
        default=None, description="For costing a plan in money as well as in gas."
    )
    currency: Currency = Currency.ILS
    note: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LineState(str, Enum):
    PENDING = "pending"
    PURCHASED = "purchased"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"


class ShoppingLine(SQLModel, table=True):
    """One thing to buy, in one of the two lists."""

    id: int | None = Field(default=None, primary_key=True)

    week_start: date = Field(index=True)
    buy_on: date | None = Field(
        default=None,
        index=True,
        description="The day it must be bought, for perishables. None means any "
        "time this week.",
    )
    item_id: int = Field(foreign_key="item.id", index=True)
    quantity: float
    unit: Unit = Unit.GRAM

    state: LineState = Field(default=LineState.PENDING, index=True)
    paid: float | None = Field(default=None, description="What it actually cost.")
    transaction_id: int | None = Field(default=None, foreign_key="transaction.id")
    replaced_by_id: int | None = Field(
        default=None, description="The substitute line created when this was unavailable."
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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

    source_id: int | None = Field(default=None, foreign_key="source.id", index=True)
    external_id: str | None = Field(
        default=None,
        index=True,
        description="The provider's own reference, when the export supplies one. "
        "Authoritative for deduplication when present.",
    )

    is_internal_transfer: bool = Field(
        default=False,
        index=True,
        description="Money moved between the household's own accounts, e.g. a "
        "bank-to-wallet top-up. Excluded from spending totals.",
    )
    transfer_peer_id: int | None = Field(
        default=None, description="The matching transaction on the other side."
    )

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

"""What the household already has, and whether it is still worth cooking.

The app was built assuming every ingredient gets bought. For a household holding
months of aid staples that is simply wrong, and it produces a shopping list that
asks for more lentils than anyone could eat. So the shopping list now subtracts
the cupboard before it asks for money.

Two things this has to get right to be trusted:

  * Spoiled stock is not stock. A sack of flour that has sat through a hot summer
    is not the asset an inventory count claims, so storage risk is computed from
    the same Q10 model the shopping list uses, and anything marked spoiled is
    excluded from cover entirely.
  * Stock is spent in the order it will go off. Oldest and most at-risk first,
    so the cupboard drains in the order that wastes least.
"""

from dataclasses import dataclass
from datetime import date

from sqlmodel import Session, select

from app.kitchen.spoilage import REFERENCE_C, keeps_for
from app.models import Item, PantryStock, StockCondition


@dataclass
class StockRisk:
    stock_id: int
    item_id: int
    name_en: str
    name_ar: str
    quantity: float
    unit: str
    days_held: int
    keeps_days: float
    fraction_used: float
    condition: StockCondition
    note: str | None = None


def available(session: Session, item_ids: set[int] | None = None) -> dict[int, float]:
    """How much of each item is on hand and edible, by item id.

    Spoiled stock is excluded: counting it would suppress a purchase the
    household actually needs to make, which is the one failure mode here that
    costs someone a meal.
    """
    statement = select(PantryStock).where(PantryStock.condition != StockCondition.SPOILED)
    if item_ids:
        statement = statement.where(PantryStock.item_id.in_(item_ids))

    out: dict[int, float] = {}
    for row in session.exec(statement):
        if row.quantity <= 0:
            continue
        out[row.item_id] = out.get(row.item_id, 0.0) + row.quantity
    return out


def consume(session: Session, item_id: int, quantity: float) -> float:
    """Spend stock, oldest and most at-risk first. Returns what was actually taken.

    Draining in that order means the cupboard empties in the order that wastes
    least, rather than leaving the doubtful sack at the back.
    """
    if quantity <= 0:
        return 0.0

    rows = [
        row
        for row in session.exec(
            select(PantryStock).where(
                PantryStock.item_id == item_id,
                PantryStock.condition != StockCondition.SPOILED,
            )
        )
        if row.quantity > 0
    ]
    # At-risk before good, then oldest first.
    rows.sort(key=lambda r: (r.condition is StockCondition.GOOD, r.acquired_on))

    remaining = quantity
    for row in rows:
        if remaining <= 0:
            break
        taken = min(row.quantity, remaining)
        row.quantity = round(row.quantity - taken, 4)
        remaining = round(remaining - taken, 4)
        session.add(row)

    return round(quantity - remaining, 4)


def add(
    session: Session,
    item: Item,
    quantity: float,
    source,
    acquired_on: date | None = None,
    note: str = "",
    parcel_id: int | None = None,
) -> PantryStock:
    row = PantryStock(
        item_id=item.id,
        quantity=quantity,
        unit=item.unit,
        source=source,
        acquired_on=acquired_on or date.today(),
        note=note,
        parcel_id=parcel_id,
    )
    session.add(row)
    return row


def assess(
    session: Session, on: date | None = None, celsius: float | None = None
) -> list[StockRisk]:
    """How much of each holding's shelf life has been used up in storage.

    This is the flour-and-moths case. Flour keeps about 270 days at 20 degrees,
    but at 30 it keeps far less, and a household that was given a sack in spring
    has an asset the app should stop describing as one.

    Nothing is marked spoiled automatically — only the household can say that.
    `at_risk` is a prompt to check, not a verdict.
    """
    today = on or date.today()
    temperature = REFERENCE_C if celsius is None else celsius

    items = {i.id: i for i in session.exec(select(Item))}
    out: list[StockRisk] = []

    for row in session.exec(select(PantryStock)):
        if row.quantity <= 0:
            continue
        item = items.get(row.item_id)
        if item is None:
            continue

        keeping = keeps_for(item.keeps_days_at_20c, item.q10, temperature)
        held = max((today - row.acquired_on).days, 0)
        fraction = round(held / keeping.days, 3) if keeping.days > 0 else 999.0

        note = None
        if row.condition is StockCondition.SPOILED:
            note = f"{item.name_en} is marked spoiled and is not counted as stock."
        elif fraction >= 1.0:
            note = (
                f"{item.name_en} has been stored {held} days. At around "
                f"{keeping.at_c:g}°C that is past the {keeping.days:g} days it "
                "normally keeps — worth checking before you plan a meal around it."
            )
        elif fraction >= 0.75:
            note = (
                f"{item.name_en} has been stored {held} of about "
                f"{keeping.days:g} days. Use it before the newer stock."
            )

        out.append(
            StockRisk(
                stock_id=row.id,
                item_id=item.id,
                name_en=item.name_en,
                name_ar=item.name_ar,
                quantity=row.quantity,
                unit=row.unit.value,
                days_held=held,
                keeps_days=keeping.days,
                fraction_used=fraction,
                condition=row.condition,
                note=note,
            )
        )

    return sorted(out, key=lambda r: r.fraction_used, reverse=True)

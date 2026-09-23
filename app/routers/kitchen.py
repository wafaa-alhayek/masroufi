from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app import sources as source_service
from app.db import get_session
from app.kitchen import get_household
from app.models import (
    CategorySource,
    Currency,
    Dish,
    DishItem,
    DishPortion,
    Household,
    Item,
    ItemRole,
    LineState,
    MealSlot,
    PlannedMeal,
    ShelfLife,
    ShoppingLine,
    Transaction,
    Unit,
)
from app.redact import normalise
from app.services import shopping

router = APIRouter()

GROCERY_CATEGORY = "groceries"


# --- schemas -----------------------------------------------------------------


class HouseholdUpdate(BaseModel):
    adults: int | None = Field(default=None, ge=0)
    children: int | None = Field(default=None, ge=0)
    child_portion_ratio: float | None = Field(default=None, gt=0, le=1)


class HouseholdOut(BaseModel):
    adults: int
    children: int
    child_portion_ratio: float
    adult_equivalents: float


class DishOut(BaseModel):
    id: int
    slug: str
    name_en: str
    name_ar: str
    default_slot: MealSlot
    source: str
    needs_review: bool


class IngredientIn(BaseModel):
    item_id: int
    qty_per_adult: float = Field(gt=0)
    optional: bool = False


class IngredientsIn(BaseModel):
    ingredients: list[IngredientIn]


class IngredientOut(BaseModel):
    item_id: int
    item_slug: str
    name_en: str
    name_ar: str
    unit: str
    qty_per_adult: float
    optional: bool


class ItemUpdate(BaseModel):
    name_en: str | None = None
    name_ar: str | None = None
    unit: Unit | None = None
    shelf_life: ShelfLife | None = Field(
        default=None,
        description="Moves the item between the daily and the weekly list.",
    )
    role: ItemRole | None = None
    purchase_step: float | None = Field(default=None, gt=0)


class PlanEntry(BaseModel):
    plan_date: date
    slot: MealSlot = MealSlot.LUNCH
    dish_id: int


class PlanOut(BaseModel):
    id: int
    plan_date: date
    slot: MealSlot
    dish_id: int
    dish_name: str


class PortionOverride(BaseModel):
    factor: float = Field(gt=0, description="1.0 is the seeded portion; 1.25 is a quarter more.")


class LineOut(BaseModel):
    id: int
    item_id: int
    item_name_en: str
    item_name_ar: str
    quantity: float
    unit: str
    buy_on: date | None
    state: LineState
    shelf_life: str
    for_dishes: list[str] = []


class ShoppingListOut(BaseModel):
    week_start: date
    daily: dict[str, list[LineOut]] = Field(
        description="Perishables, keyed by the day they must be bought. Refrigeration "
        "is scarce, so these are bought the day they are cooked."
    )
    weekly: list[LineOut] = Field(
        description="Everything that keeps, aggregated into one shop for the week."
    )


class PurchaseIn(BaseModel):
    paid: float = Field(gt=0, description="What it actually cost.")
    currency: Currency = Currency.ILS
    source: str = Field(default="manual", description="Provider slug it was paid from.")
    vendor_note: str | None = Field(
        default=None, description="Shop name, if you want it in the transaction note."
    )
    purchased_on: date | None = None
    quantity: float | None = Field(
        default=None, gt=0, description="If you bought a different amount than listed."
    )


class PurchaseOut(BaseModel):
    line_id: int
    transaction_id: int
    quantity: float
    unit: str
    unit_price: float = Field(
        description="Price per single unit. Kept at six decimals because a price "
        "per gram is a small number and rounding it away would make the price "
        "dataset useless."
    )
    price_per_1000: float | None = Field(
        default=None,
        description="Price per kg or litre, which is how shops quote and how the "
        "WFP and PCBS datasets are published. Null for items counted in pieces.",
    )


class UnavailableIn(BaseModel):
    substitute_item_id: int | None = Field(
        default=None, description="Omit to just record that it was not available."
    )


# --- household ---------------------------------------------------------------


@router.get("/household", response_model=HouseholdOut)
def read_household(session: Session = Depends(get_session)) -> HouseholdOut:
    return _household_out(get_household(session))


@router.patch("/household", response_model=HouseholdOut)
def update_household(
    update: HouseholdUpdate, session: Session = Depends(get_session)
) -> HouseholdOut:
    household = get_household(session)
    for field_name, value in update.model_dump(exclude_none=True).items():
        setattr(household, field_name, value)
    session.add(household)
    session.commit()
    session.refresh(household)
    return _household_out(household)


def _household_out(household: Household) -> HouseholdOut:
    return HouseholdOut(
        adults=household.adults,
        children=household.children,
        child_portion_ratio=household.child_portion_ratio,
        adult_equivalents=round(household.adult_equivalents, 2),
    )


# --- dishes and the plan -----------------------------------------------------


@router.get("/dishes", response_model=list[DishOut])
def list_dishes(
    slot: MealSlot | None = None, session: Session = Depends(get_session)
) -> list[Dish]:
    statement = select(Dish)
    if slot is not None:
        statement = statement.where(Dish.default_slot == slot)
    return list(session.exec(statement.order_by(Dish.name_en)))


@router.get("/items", response_model=list[Item])
def list_items(session: Session = Depends(get_session)) -> list[Item]:
    """The grocery catalogue. Editable: the seeded quantities and shelf lives are
    a starting point set by a developer, not by someone who shops here."""
    return list(session.exec(select(Item).order_by(Item.name_en)))


@router.get("/dishes/{dish_id}/ingredients", response_model=list[IngredientOut])
def read_ingredients(dish_id: int, session: Session = Depends(get_session)) -> list[IngredientOut]:
    if session.get(Dish, dish_id) is None:
        raise HTTPException(404, "No such dish.")
    items = {i.id: i for i in session.exec(select(Item))}
    return [
        IngredientOut(
            item_id=row.item_id,
            item_slug=items[row.item_id].slug,
            name_en=items[row.item_id].name_en,
            name_ar=items[row.item_id].name_ar,
            unit=items[row.item_id].unit.value,
            qty_per_adult=row.qty_per_adult,
            optional=row.optional,
        )
        for row in session.exec(select(DishItem).where(DishItem.dish_id == dish_id))
        if row.item_id in items
    ]


@router.put("/dishes/{dish_id}/ingredients", response_model=list[IngredientOut])
def set_ingredients(
    dish_id: int, body: IngredientsIn, session: Session = Depends(get_session)
) -> list[IngredientOut]:
    """Replace a dish's ingredients with the household's own version.

    The seeded quantities were set by a developer, not a cook. This is how they
    get corrected, and correcting them is the difference between the list saving
    money and wasting it.
    """
    dish = session.get(Dish, dish_id)
    if dish is None:
        raise HTTPException(404, "No such dish.")
    if not body.ingredients:
        raise HTTPException(422, "A dish needs at least one ingredient.")

    known = {i.id for i in session.exec(select(Item))}
    unknown = [i.item_id for i in body.ingredients if i.item_id not in known]
    if unknown:
        raise HTTPException(422, f"Unknown item ids: {unknown}")

    seen: set[int] = set()
    for row in body.ingredients:
        if row.item_id in seen:
            raise HTTPException(422, f"Item {row.item_id} is listed twice.")
        seen.add(row.item_id)

    for existing in session.exec(select(DishItem).where(DishItem.dish_id == dish_id)):
        session.delete(existing)
    session.flush()

    session.add_all(
        DishItem(
            dish_id=dish_id,
            item_id=row.item_id,
            qty_per_adult=row.qty_per_adult,
            optional=row.optional,
        )
        for row in body.ingredients
    )
    dish.edited_by_household = True
    dish.needs_review = False
    session.add(dish)
    session.commit()
    return read_ingredients(dish_id, session)


@router.patch("/items/{item_id}", response_model=Item)
def update_item(item_id: int, body: ItemUpdate, session: Session = Depends(get_session)) -> Item:
    """Correct a catalogue entry — its unit, how it is sold, or how long it keeps.

    Shelf life is the consequential one: changing it moves the item between the
    daily and the weekly list.
    """
    item = session.get(Item, item_id)
    if item is None:
        raise HTTPException(404, "No such item.")

    for field_name, value in body.model_dump(exclude_none=True).items():
        setattr(item, field_name, value)
    item.needs_review = False
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@router.post("/plan", response_model=PlanOut)
def add_meal(entry: PlanEntry, session: Session = Depends(get_session)) -> PlanOut:
    dish = session.get(Dish, entry.dish_id)
    if dish is None:
        raise HTTPException(404, "No such dish.")

    existing = session.exec(
        select(PlannedMeal).where(
            PlannedMeal.plan_date == entry.plan_date, PlannedMeal.slot == entry.slot
        )
    ).first()
    if existing is not None:
        existing.dish_id = dish.id
        meal = existing
    else:
        meal = PlannedMeal(plan_date=entry.plan_date, slot=entry.slot, dish_id=dish.id)
    session.add(meal)
    session.commit()
    session.refresh(meal)
    return PlanOut(
        id=meal.id,
        plan_date=meal.plan_date,
        slot=meal.slot,
        dish_id=meal.dish_id,
        dish_name=dish.name_en,
    )


@router.get("/plan", response_model=list[PlanOut])
def read_plan(
    week_start: date = Query(..., description="Any date; the week runs seven days from it."),
    session: Session = Depends(get_session),
) -> list[PlanOut]:
    start, end = shopping.week_bounds(week_start)
    meals = list(
        session.exec(
            select(PlannedMeal)
            .where(PlannedMeal.plan_date >= start, PlannedMeal.plan_date <= end)
            .order_by(PlannedMeal.plan_date)
        )
    )
    names = {d.id: d.name_en for d in session.exec(select(Dish))}
    return [
        PlanOut(
            id=m.id,
            plan_date=m.plan_date,
            slot=m.slot,
            dish_id=m.dish_id,
            dish_name=names.get(m.dish_id, "?"),
        )
        for m in meals
    ]


@router.delete("/plan/{meal_id}")
def remove_meal(meal_id: int, session: Session = Depends(get_session)) -> dict:
    meal = session.get(PlannedMeal, meal_id)
    if meal is None:
        raise HTTPException(404, "No such planned meal.")
    session.delete(meal)
    session.commit()
    return {"deleted": meal_id}


@router.put("/dishes/{dish_id}/portion", response_model=dict)
def set_portion(
    dish_id: int, override: PortionOverride, session: Session = Depends(get_session)
) -> dict:
    """Remember that this household needs more or less than the seeded portion."""
    if session.get(Dish, dish_id) is None:
        raise HTTPException(404, "No such dish.")

    existing = session.exec(select(DishPortion).where(DishPortion.dish_id == dish_id)).first()
    if existing is None:
        existing = DishPortion(dish_id=dish_id, factor=override.factor)
    else:
        existing.factor = override.factor
    session.add(existing)
    session.commit()
    return {"dish_id": dish_id, "factor": override.factor}


# --- the shopping list -------------------------------------------------------


@router.post("/shopping-list")
def build_list(
    week_start: date = Query(...),
    session: Session = Depends(get_session),
) -> dict:
    """Generate or refresh the week's lists from the plan.

    Lines already purchased, substituted or skipped are preserved, so changing
    Thursday's meal never undoes Monday's shop.
    """
    created, kept = shopping.regenerate(session, week_start)
    return {"week_start": week_start, "lines_created": created, "lines_kept": kept}


@router.get("/shopping-list", response_model=ShoppingListOut)
def read_list(
    week_start: date = Query(...),
    include_settled: bool = False,
    session: Session = Depends(get_session),
) -> ShoppingListOut:
    statement = select(ShoppingLine).where(ShoppingLine.week_start == week_start)
    if not include_settled:
        statement = statement.where(ShoppingLine.state == LineState.PENDING)
    lines = list(session.exec(statement))

    items = {i.id: i for i in session.exec(select(Item))}
    needs = {
        (n.item.id, n.buy_on): n.dishes for n in shopping.compute_needs(session, week_start)
    }

    daily: dict[str, list[LineOut]] = {}
    weekly: list[LineOut] = []
    for line in sorted(lines, key=lambda line: (line.buy_on or date.min, line.id)):
        item = items.get(line.item_id)
        if item is None:
            continue
        out = LineOut(
            id=line.id,
            item_id=item.id,
            item_name_en=item.name_en,
            item_name_ar=item.name_ar,
            quantity=line.quantity,
            unit=line.unit.value,
            buy_on=line.buy_on,
            state=line.state,
            shelf_life=item.shelf_life.value,
            for_dishes=needs.get((item.id, line.buy_on), []),
        )
        if line.buy_on is None:
            weekly.append(out)
        else:
            daily.setdefault(line.buy_on.isoformat(), []).append(out)

    return ShoppingListOut(week_start=week_start, daily=daily, weekly=weekly)


@router.get("/shopping-list/{line_id}/substitutes", response_model=list[Item])
def line_substitutes(line_id: int, session: Session = Depends(get_session)) -> list[Item]:
    line = session.get(ShoppingLine, line_id)
    if line is None:
        raise HTTPException(404, "No such shopping line.")
    item = session.get(Item, line.item_id)
    if item is None:
        raise HTTPException(404, "The item on this line no longer exists.")
    return shopping.substitutes(session, item)


@router.post("/shopping-list/{line_id}/unavailable")
def mark_unavailable(
    line_id: int, body: UnavailableIn, session: Session = Depends(get_session)
) -> dict:
    """Record that a planned item was not at the shop, optionally swapping it.

    Worth recording even without a substitute: over time this is real data about
    what is scarce and when.
    """
    line = session.get(ShoppingLine, line_id)
    if line is None:
        raise HTTPException(404, "No such shopping line.")
    if line.state is not LineState.PENDING:
        raise HTTPException(409, f"This line is already {line.state.value}.")

    line.state = LineState.UNAVAILABLE
    session.add(line)

    replacement_id = None
    if body.substitute_item_id is not None:
        substitute = session.get(Item, body.substitute_item_id)
        if substitute is None:
            raise HTTPException(404, "No such substitute item.")
        replacement = ShoppingLine(
            week_start=line.week_start,
            buy_on=line.buy_on,
            item_id=substitute.id,
            quantity=line.quantity,
            unit=substitute.unit,
        )
        session.add(replacement)
        session.flush()
        line.replaced_by_id = replacement.id
        session.add(line)
        replacement_id = replacement.id

    session.commit()
    return {"line_id": line_id, "state": line.state.value, "replacement_line_id": replacement_id}


@router.post("/shopping-list/{line_id}/purchase", response_model=PurchaseOut)
def confirm_purchase(
    line_id: int, body: PurchaseIn, session: Session = Depends(get_session)
) -> PurchaseOut:
    """Confirm a planned item was bought, and record it as a transaction.

    This is also where the price dataset starts: a quantity, a price, a date and
    a shop is an item-level price observation, available before receipt reading
    exists.
    """
    line = session.get(ShoppingLine, line_id)
    if line is None:
        raise HTTPException(404, "No such shopping line.")
    if line.state is not LineState.PENDING:
        raise HTTPException(409, f"This line is already {line.state.value}.")

    item = session.get(Item, line.item_id)
    if item is None:
        raise HTTPException(404, "The item on this line no longer exists.")

    provider = source_service.by_slug(session, body.source)
    if provider is None:
        raise HTTPException(422, f"Unknown source {body.source!r}.")

    quantity = body.quantity or line.quantity
    booked_on = body.purchased_on or line.buy_on or line.week_start
    note = body.vendor_note or f"{item.name_ar} ({quantity:g} {item.unit.value})"

    transaction = Transaction(
        booked_on=booked_on,
        amount=-abs(body.paid),
        currency=body.currency,
        note=note,
        note_key=normalise(note),
        source_id=provider.id,
        category=GROCERY_CATEGORY,
        category_source=CategorySource.USER,
        confidence=1.0,
        needs_review=False,
        import_batch=f"plan-{line.week_start.isoformat()}",
    )
    session.add(transaction)
    session.flush()

    line.state = LineState.PURCHASED
    line.paid = abs(body.paid)
    line.quantity = quantity
    line.transaction_id = transaction.id
    session.add(line)
    session.commit()

    unit_price = abs(body.paid) / quantity if quantity else 0.0
    bulk_units = {Unit.GRAM, Unit.ML}
    return PurchaseOut(
        line_id=line_id,
        transaction_id=transaction.id,
        quantity=quantity,
        unit=item.unit.value,
        unit_price=round(unit_price, 6),
        price_per_1000=round(unit_price * 1000, 2) if item.unit in bulk_units else None,
    )

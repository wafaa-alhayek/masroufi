"""The cupboard, aid parcels, and what to cook given both."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.db import get_session
from app.idempotency import remember, replay
from app.deps import get_temperature_source
from app.config import settings
from app.models import (
    AidParcel,
    Item,
    ItemFeeling,
    ItemPreference,
    LineState,
    MealSlot,
    PantryStock,
    ShoppingLine,
    StockCondition,
    StockSource,
)
from app.services import pantry as pantry_service
from app.services import suggest as suggest_service
from app.weather import TemperatureSource

router = APIRouter()


# --- schemas -----------------------------------------------------------------


class StockIn(BaseModel):
    key: str | None = Field(
        default=None,
        max_length=128,
        description="Client-generated, so a replayed offline queue cannot record "
        "this twice.",
    )
    item_id: int
    quantity: float = Field(gt=0, description="In the item's own unit.")
    source: StockSource = StockSource.UNKNOWN
    acquired_on: date | None = None
    note: str = ""


class StockOut(BaseModel):
    id: int
    item_id: int
    name_en: str
    name_ar: str
    quantity: float
    unit: str
    source: StockSource
    condition: StockCondition
    acquired_on: date
    days_held: int
    keeps_days: float
    fraction_used: float = Field(
        description="How much of its shelf life storage has used up. Over 1.0 means "
        "it is past what it normally keeps at this temperature."
    )
    note: str | None = None


class PantryOut(BaseModel):
    at_c: float
    temperature_estimated: bool
    items: list[StockOut]
    warnings: list[str] = Field(
        description="Only holdings worth checking. Empty means nothing is at risk."
    )


class ParcelLine(BaseModel):
    item_id: int
    quantity: float = Field(gt=0)


class ParcelIn(BaseModel):
    key: str | None = Field(
        default=None,
        max_length=128,
        description="Client-generated, so a replayed offline queue cannot record "
        "this twice.",
    )
    received_on: date | None = None
    label: str = ""
    lines: list[ParcelLine] = Field(
        description="Aid arrives as a bundle, so it is recorded as one."
    )


class ConditionIn(BaseModel):
    condition: StockCondition


class FeelingIn(BaseModel):
    key: str | None = Field(
        default=None,
        max_length=128,
        description="Client-generated, so a replayed offline queue cannot record "
        "this twice.",
    )
    feeling: ItemFeeling = Field(
        description="WEARY pushes an item down the suggestions. No reason is asked for."
    )


class MissingOut(BaseModel):
    item_id: int
    name_en: str
    name_ar: str
    quantity: float
    unit: str
    role: str
    cost: float | None = Field(
        default=None, description="Null when no price has been recorded for this item."
    )


class SuggestionOut(BaseModel):
    dish_id: int
    name_en: str
    name_ar: str
    slot: MealSlot
    stock_cover: float = Field(description="Fraction of the dish already in the cupboard.")
    missing: list[MissingOut]
    repetition: int = Field(description="Times its main ingredients ran in the last 14 days.")
    weary_items: list[str]
    gas_kg: float
    missing_cost: float | None = Field(
        default=None,
        description="What the missing ingredients would cost, where prices are known.",
    )
    missing_cost_partial: bool = Field(
        default=False,
        description="True when some missing item has no recorded price, so the total "
        "understates. A figure is never quoted in `why` when this is set.",
    )
    flavour_only: bool = Field(
        description="Everything missing is a spice, paste or oil — a different meal "
        "for very little money."
    )
    score: float
    why: str


class SuggestionsOut(BaseModel):
    suggestions: list[SuggestionOut]
    withheld: list[SuggestionOut] = Field(
        description="Dishes built on something the household has had enough of. "
        "Returned so a screen can say how many are hidden, never to argue for them."
    )
    withheld_because: list[str] = Field(
        description="The items behind those exclusions, for a plain explanation."
    )


# --- the cupboard ------------------------------------------------------------


@router.post("/stock", response_model=StockOut)
async def add_stock(
    body: StockIn,
    session: Session = Depends(get_session),
    temperatures: TemperatureSource = Depends(get_temperature_source),
) -> StockOut:
    """Record something the household already has."""
    item = session.get(Item, body.item_id)
    if item is None:
        raise HTTPException(404, "No such item.")

    pantry_service.add(
        session,
        item,
        body.quantity,
        body.source,
        acquired_on=body.acquired_on,
        note=body.note,
    )
    session.commit()

    view = await read_pantry(None, session, temperatures)
    latest = max((row for row in view.items if row.item_id == item.id), key=lambda r: r.id)
    return latest


@router.get("/stock", response_model=PantryOut)
async def read_pantry(
    on: date | None = Query(None, description="Defaults to today."),
    session: Session = Depends(get_session),
    temperatures: TemperatureSource = Depends(get_temperature_source),
) -> PantryOut:
    """Everything on hand, with how much of its shelf life storage has used up.

    This is the flour-and-moths case. A sack given out in spring is not in spring
    condition by August, and an inventory that says otherwise is worse than none.
    """
    day = on or date.today()
    reading = (await temperatures.daily_max(day, day)).get(day)
    celsius = reading.max_c if reading else None

    risks = pantry_service.assess(session, on=day, celsius=celsius)
    return PantryOut(
        at_c=reading.max_c if reading else 20.0,
        temperature_estimated=reading.estimated if reading else True,
        items=[
            StockOut(
                id=r.stock_id,
                item_id=r.item_id,
                name_en=r.name_en,
                name_ar=r.name_ar,
                quantity=r.quantity,
                unit=r.unit,
                source=_source_of(session, r.stock_id),
                condition=r.condition,
                acquired_on=_acquired_of(session, r.stock_id),
                days_held=r.days_held,
                keeps_days=r.keeps_days,
                fraction_used=r.fraction_used,
                note=r.note,
            )
            for r in risks
        ],
        warnings=[r.note for r in risks if r.note],
    )


def _source_of(session: Session, stock_id: int) -> StockSource:
    row = session.get(PantryStock, stock_id)
    return row.source if row else StockSource.UNKNOWN


def _acquired_of(session: Session, stock_id: int) -> date:
    row = session.get(PantryStock, stock_id)
    return row.acquired_on if row else date.today()


@router.patch("/stock/{stock_id}/condition")
def set_condition(
    stock_id: int, body: ConditionIn, session: Session = Depends(get_session)
) -> dict:
    """Only the household can say something has gone off.

    The app flags risk from temperature and time; it never decides this itself.
    Stock marked spoiled stops counting towards the shopping list, so the household
    is asked to buy what it actually needs.
    """
    row = session.get(PantryStock, stock_id)
    if row is None:
        raise HTTPException(404, "No such stock.")
    row.condition = body.condition
    session.add(row)
    session.commit()
    return {"stock_id": stock_id, "condition": row.condition.value}


@router.post("/parcel")
def record_parcel(body: ParcelIn, session: Session = Depends(get_session)) -> dict:
    """Record an aid delivery in one go.

    Aid arrives as a bundle of staples, not as a shopping trip. Asking a household
    to enter eight items separately means nobody ever enters any of them.
    """
    seen = replay(session, body.key, "parcel")
    if seen is not None:
        return seen

    if not body.lines:
        raise HTTPException(422, "A parcel needs at least one item.")

    known = {i.id: i for i in session.exec(select(Item))}
    unknown = [line.item_id for line in body.lines if line.item_id not in known]
    if unknown:
        raise HTTPException(422, f"Unknown item ids: {unknown}")

    parcel = AidParcel(received_on=body.received_on or date.today(), label=body.label)
    session.add(parcel)
    session.flush()

    for line in body.lines:
        pantry_service.add(
            session,
            known[line.item_id],
            line.quantity,
            StockSource.AID,
            acquired_on=parcel.received_on,
            parcel_id=parcel.id,
        )
    out = {
        "parcel_id": parcel.id,
        "received_on": parcel.received_on,
        "items_recorded": len(body.lines),
    }
    remember(session, body.key, "parcel", out)
    session.commit()
    return out


# --- how people feel about what they have ------------------------------------


@router.put("/items/{item_id}/feeling")
def set_feeling(
    item_id: int, body: FeelingIn, session: Session = Depends(get_session)
) -> dict:
    """Mark something the household has had enough of.

    No reason is requested and none is stored. A weary item drops down the
    suggestions and is never argued back up, however cheap it is.
    """
    if session.get(Item, item_id) is None:
        raise HTTPException(404, "No such item.")

    existing = session.exec(
        select(ItemPreference).where(ItemPreference.item_id == item_id)
    ).first()
    if existing is None:
        existing = ItemPreference(item_id=item_id, feeling=body.feeling)
    else:
        existing.feeling = body.feeling
    session.add(existing)
    session.commit()
    return {"item_id": item_id, "feeling": existing.feeling.value}


@router.get("/items/feelings")
def list_feelings(session: Session = Depends(get_session)) -> list[dict]:
    names = {i.id: i for i in session.exec(select(Item))}
    return [
        {
            "item_id": p.item_id,
            "name_en": names[p.item_id].name_en if p.item_id in names else "?",
            "feeling": p.feeling.value,
        }
        for p in session.exec(select(ItemPreference))
        if p.feeling is not ItemFeeling.NEUTRAL
    ]


# --- what to cook ------------------------------------------------------------


@router.get("/suggestions", response_model=SuggestionsOut)
def suggestions(
    on: date | None = None,
    slot: MealSlot | None = None,
    limit: int = Query(8, ge=1, le=50),
    include_weary: bool = Query(
        False, description='Set when the household asks to see the hidden ones anyway.'
    ),
    session: Session = Depends(get_session),
) -> SuggestionsOut:
    """What to cook, weighing the cupboard against what people are tired of.

    Dishes built on something the household has had enough of are **withheld**, not
    ranked lower, and reported separately with a count so a screen can say how many
    are hidden and why. An unexplained short list looks broken, and hiding the
    reason is its own kind of nudge.

    `flavour_only` is the one to surface first. It means the cupboard already covers
    the dish except for a spice or a paste — the cheapest way to eat something that
    does not taste like the last two weeks.
    """
    offered, withheld = suggest_service.suggest(
        session, on=on, slot=slot, limit=limit, include_weary=include_weary
    )
    return SuggestionsOut(
        suggestions=[_suggestion_out(s) for s in offered],
        withheld=[_suggestion_out(s) for s in withheld],
        withheld_because=sorted({name for s in withheld for name in s.weary_items}),
    )


def _suggestion_out(s) -> "SuggestionOut":
    return SuggestionOut(
            dish_id=s.dish_id,
            name_en=s.name_en,
            name_ar=s.name_ar,
            slot=s.slot,
            stock_cover=s.stock_cover,
            missing=[MissingOut(**vars(m)) for m in s.missing],
            repetition=s.repetition,
            weary_items=s.weary_items,
            gas_kg=s.gas_kg,
            missing_cost=s.missing_cost,
            missing_cost_partial=s.missing_cost_partial,
            flavour_only=s.flavour_only,
            score=s.score,
            why=s.why,
        )

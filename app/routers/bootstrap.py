"""Everything the app needs to work with no connection afterwards.

First launch may assume a network. Nothing after it may. So one call returns the
whole working set, and the client stores it.

The design decision worth stating, because it shapes the client: **the server ships
the outputs of its models, not the models.** Porting the Q10 shelf-life curve, the
portion maths and the price-decay weighting to TypeScript would mean two
implementations of the rules this app's correctness claims rest on, and they would
drift. Instead each item carries `keeps_days` already evaluated at each of the
week's temperatures, so the client's rule is one subtraction and one comparison —
"does it keep longer than the days I would hold it" — with no physics on the device.

What the client can therefore do offline:
  * re-derive the two lists after a plan change, from `keeps_days_by_day`
  * subtract stock, which is arithmetic over numbers already shipped
  * show prices, from estimates already computed
  * queue writes, and replay them safely (see the idempotency key on each write)

What it cannot, and should not pretend to: refresh a price estimate, or get a
forecast for a week it has no temperatures for. Both degrade honestly — a price
goes stale and says so, a missing day falls back to the monthly normal.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.config import settings
from app.db import get_session
from app.deps import get_temperature_source
from app.kitchen import get_household
from app.kitchen.spoilage import DAILY_THRESHOLD_DAYS, REFERENCE_C, keeps_for
from app.models import (
    Dish,
    DishAlias,
    DishItem,
    DishPortion,
    GasBudget,
    Item,
    ItemFeeling,
    ItemPreference,
    PantryStock,
    PlannedMeal,
    ShoppingLine,
    Source,
    StockCondition,
)
from app.services import prices as price_service
from app.services import shopping
from app.weather import CLIMATE_NORMALS_C, TemperatureSource

router = APIRouter()

# Bumped when the shape changes, so a stored bundle can be recognised as old.
BUNDLE_VERSION = 1


class ItemBundle(BaseModel):
    id: int
    slug: str
    name_en: str
    name_ar: str
    unit: str
    role: str
    shelf_life: str
    purchase_step: float
    keeps_days_at_20c: float
    q10: float
    keeps_days_by_day: dict[str, float] = Field(
        description="Effective shelf life on each day of the requested week, already "
        "evaluated. The client compares this against how many days it would hold the "
        "item — no shelf-life model needs to exist on the device."
    )


class DishBundle(BaseModel):
    id: int
    slug: str
    name_en: str
    name_ar: str
    default_slot: str
    full_flame_minutes: int
    simmer_minutes: int
    burners: int
    gas_kg: float
    portion_factor: float = 1.0
    ingredients: dict[int, float] = Field(
        description="item id to quantity per adult portion."
    )
    aliases_ar: list[str] = Field(
        default_factory=list,
        description="Other names people search for, so the offline picker finds it too.",
    )


class StockBundle(BaseModel):
    id: int
    item_id: int
    quantity: float
    unit: str
    source: str
    condition: str
    acquired_on: date


class PriceBundle(BaseModel):
    item_id: int
    price: float
    quoted_per: float
    currency: str
    low: float
    high: float
    newest_days_old: int
    stale: bool
    certainty: str


class ParcelTemplateBundle(BaseModel):
    source: str
    lines: list[dict]


class PlanBundle(BaseModel):
    id: int
    plan_date: date
    slot: str
    dish_id: int


class LineBundle(BaseModel):
    id: int
    item_id: int
    quantity: float
    required: float
    from_stock: float
    unit: str
    buy_on: date | None
    covers_through: date | None
    state: str


class HouseholdBundle(BaseModel):
    adults: int
    children: int
    child_portion_ratio: float
    adult_equivalents: float


class RulesBundle(BaseModel):
    daily_threshold_days: float = Field(
        description="An item that keeps less than this is always bought on the day."
    )
    reference_c: float
    burner_kg_per_hour: float
    simmer_kg_per_hour: float
    price_stale_after_days: int
    confidence_threshold: float


class Bootstrap(BaseModel):
    bundle_version: int
    generated_at: date
    week_start: date
    weather_source: str
    temperatures: dict[str, float] = Field(description="Daily maximum for the week.")
    temperatures_estimated: dict[str, bool]
    climate_normals_c: dict[int, float] = Field(
        description="Monthly fallback, so the client can still split a list for a "
        "week it has no forecast for."
    )
    rules: RulesBundle
    household: HouseholdBundle
    sources: list[dict]
    categories: dict[str, str]
    items: list[ItemBundle]
    dishes: list[DishBundle]
    stock: list[StockBundle]
    weary_item_ids: list[int]
    prices: list[PriceBundle]
    plan: list[PlanBundle]
    lines: list[LineBundle]
    parcel_template: ParcelTemplateBundle = Field(
        description="What a new parcel sheet opens with, so recording one works offline."
    )
    dishes_offline_only: bool = Field(
        description="True when `dishes` is the curated offline set rather than the "
        "whole library. The rest are searchable when there is a connection."
    )


@router.get("", response_model=Bootstrap)
async def bootstrap(
    week_start: date | None = Query(
        None, description="Defaults to the current week, starting today."
    ),
    session: Session = Depends(get_session),
    temperatures: TemperatureSource = Depends(get_temperature_source),
) -> Bootstrap:
    """One payload the client stores and then works from with no connection."""
    from app.kitchen import fuel
    from app.taxonomy import CATEGORIES

    start = week_start or date.today()
    end = start + timedelta(days=6)
    days = [start + timedelta(days=offset) for offset in range(7)]

    readings = await temperatures.daily_max(start, end)

    items = list(session.exec(select(Item)))
    item_bundles = [
        ItemBundle(
            id=item.id,
            slug=item.slug,
            name_en=item.name_en,
            name_ar=item.name_ar,
            unit=item.unit.value,
            role=item.role.value,
            shelf_life=item.shelf_life.value,
            purchase_step=item.purchase_step,
            keeps_days_at_20c=item.keeps_days_at_20c,
            q10=item.q10,
            keeps_days_by_day={
                day.isoformat(): keeps_for(
                    item.keeps_days_at_20c,
                    item.q10,
                    readings[day].max_c if day in readings else REFERENCE_C,
                ).days
                for day in days
            },
        )
        for item in items
    ]

    ingredients: dict[int, dict[int, float]] = {}
    for row in session.exec(select(DishItem)):
        ingredients.setdefault(row.dish_id, {})[row.item_id] = row.qty_per_adult

    factors = {p.dish_id: p.factor for p in session.exec(select(DishPortion))}

    # Only the curated set travels. The rest are searchable online, which keeps the
    # bundle small enough to download on a poor connection.
    dish_query = select(Dish).where(
        Dish.needs_review == False,  # noqa: E712
        Dish.in_offline_bundle == True,  # noqa: E712
    )
    aliases: dict[int, list[str]] = {}
    for alias in session.exec(select(DishAlias)):
        aliases.setdefault(alias.dish_id, []).append(alias.name)

    dish_bundles = [
        DishBundle(
            id=dish.id,
            slug=dish.slug,
            name_en=dish.name_en,
            name_ar=dish.name_ar,
            default_slot=dish.default_slot.value,
            full_flame_minutes=dish.full_flame_minutes,
            simmer_minutes=dish.simmer_minutes,
            burners=dish.burners,
            gas_kg=fuel.estimate(
                dish.full_flame_minutes, dish.simmer_minutes, dish.burners
            ).kg,
            portion_factor=factors.get(dish.id, 1.0),
            ingredients=ingredients.get(dish.id, {}),
            aliases_ar=aliases.get(dish.id, []),
        )
        for dish in session.exec(dish_query)
    ]

    by_id = {item.id: item for item in items}
    estimates = price_service.estimates(session, by_id)

    household = get_household(session)

    return Bootstrap(
        bundle_version=BUNDLE_VERSION,
        generated_at=date.today(),
        week_start=start,
        weather_source="open-meteo" if settings.weather_enabled else "climate-normals",
        temperatures={
            day.isoformat(): readings[day].max_c for day in days if day in readings
        },
        temperatures_estimated={
            day.isoformat(): readings[day].estimated for day in days if day in readings
        },
        climate_normals_c=CLIMATE_NORMALS_C,
        rules=RulesBundle(
            daily_threshold_days=DAILY_THRESHOLD_DAYS,
            reference_c=REFERENCE_C,
            burner_kg_per_hour=settings.burner_kg_per_hour,
            simmer_kg_per_hour=settings.simmer_kg_per_hour,
            price_stale_after_days=settings.price_stale_after_days,
            confidence_threshold=settings.confidence_threshold,
        ),
        household=HouseholdBundle(
            adults=household.adults,
            children=household.children,
            child_portion_ratio=household.child_portion_ratio,
            adult_equivalents=round(household.adult_equivalents, 3),
        ),
        sources=[
            {"id": s.id, "slug": s.slug, "name": s.name, "kind": s.kind.value}
            for s in session.exec(select(Source))
        ],
        categories=CATEGORIES,
        items=item_bundles,
        dishes=dish_bundles,
        stock=[
            StockBundle(
                id=row.id,
                item_id=row.item_id,
                quantity=row.quantity,
                unit=row.unit.value,
                source=row.source.value,
                condition=row.condition.value,
                acquired_on=row.acquired_on,
            )
            for row in session.exec(select(PantryStock))
            if row.quantity > 0 and row.condition is not StockCondition.SPOILED
        ],
        weary_item_ids=[
            p.item_id
            for p in session.exec(select(ItemPreference))
            if p.feeling is ItemFeeling.WEARY
        ],
        prices=[
            PriceBundle(
                item_id=item_id,
                price=est.price,
                quoted_per=est.quoted_per,
                currency=est.currency.value,
                low=est.low,
                high=est.high,
                newest_days_old=est.newest_days_old,
                stale=est.stale,
                certainty=est.certainty,
            )
            for item_id, est in estimates.items()
        ],
        plan=[
            PlanBundle(
                id=meal.id,
                plan_date=meal.plan_date,
                slot=meal.slot.value,
                dish_id=meal.dish_id,
            )
            for meal in session.exec(
                select(PlannedMeal).where(
                    PlannedMeal.plan_date >= start, PlannedMeal.plan_date <= end
                )
            )
        ],
        parcel_template=_parcel_template(session),
        dishes_offline_only=True,
        lines=[
            LineBundle(
                id=line.id,
                item_id=line.item_id,
                quantity=line.quantity,
                required=line.required,
                from_stock=line.from_stock,
                unit=line.unit.value,
                buy_on=line.buy_on,
                covers_through=line.covers_through,
                state=line.state.value,
            )
            for line in session.exec(
                select(ShoppingLine).where(ShoppingLine.week_start == start)
            )
        ],
    )


def _parcel_template(session: Session) -> ParcelTemplateBundle:
    from app.kitchen import parcels

    lines, source = parcels.template(session)
    return ParcelTemplateBundle(source=source, lines=[vars(line) for line in lines])

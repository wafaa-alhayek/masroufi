"""The parcel sheet a household starts from.

Parcels are broadly alike, and a sheet pre-filled with what arrived last time is
much quicker to correct than one that starts empty — which matters because the
alternative is nobody recording a parcel at all, and then the shopping list asks
them to buy what they were given.

The template is a starting point in both directions: it must be editable, and
anything the household removes or changes is reflected next time, because next
time starts from what they actually recorded.
"""

from dataclasses import dataclass

from sqlmodel import Session, select

from app.models import AidParcel, Item, PantryStock

# A generic food-parcel shape, for a household with no history yet. Quantities are
# the order of magnitude a parcel tends to hold, not a claim about any particular
# distribution — the point is that the sheet opens with plausible rows to correct
# rather than an empty form.
DEFAULT_TEMPLATE: tuple[tuple[str, float], ...] = (
    ("flour", 10000),
    ("rice", 5000),
    ("lentils", 3000),
    ("chickpeas", 2000),
    ("veg_oil", 1500),
    ("sugar", 2000),
    ("salt", 1000),
    ("milk_powder", 800),
    ("canned_tuna", 600),
    ("canned_fava", 1600),
    ("tea", 200),
    ("dates", 500),
)


@dataclass
class TemplateLine:
    item_id: int
    slug: str
    name_en: str
    name_ar: str
    unit: str
    quantity: float


def template(session: Session) -> tuple[list[TemplateLine], str]:
    """The lines to pre-fill, and where they came from.

    Returns ("last_parcel") when it is built from what this household recorded last
    time, or ("default") for a household with no parcel history.
    """
    items = {item.id: item for item in session.exec(select(Item))}
    by_slug = {item.slug: item for item in items.values()}

    latest = session.exec(
        select(AidParcel).order_by(AidParcel.received_on.desc(), AidParcel.id.desc())
    ).first()

    if latest is not None:
        rows = list(
            session.exec(select(PantryStock).where(PantryStock.parcel_id == latest.id))
        )
        lines = [
            _line(items[row.item_id], row.quantity)
            for row in rows
            if row.item_id in items
        ]
        if lines:
            return lines, "last_parcel"

    return (
        [
            _line(by_slug[slug], quantity)
            for slug, quantity in DEFAULT_TEMPLATE
            if slug in by_slug
        ],
        "default",
    )


def _line(item: Item, quantity: float) -> TemplateLine:
    return TemplateLine(
        item_id=item.id,
        slug=item.slug,
        name_en=item.name_en,
        name_ar=item.name_ar,
        unit=item.unit.value,
        quantity=quantity,
    )

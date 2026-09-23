"""Meal planning and the shopping lists that come out of it."""

from sqlmodel import Session, select

from app.models import Dish, DishItem, DishSource, Household, Item

from .catalogue import seed_items
from .dishes import SEED_DISHES

__all__ = ["ensure_kitchen", "get_household"]


def ensure_kitchen(session: Session) -> None:
    """Seed the catalogue and dishes. Safe to call on every startup.

    Only fills in what is missing, and never touches a row a household has
    edited — the seeds are a starting point, not the truth.
    """
    known_items = {i.slug: i for i in session.exec(select(Item))}
    new_items = [i for i in seed_items() if i.slug not in known_items]
    if new_items:
        session.add_all(new_items)
        session.commit()
        known_items = {i.slug: i for i in session.exec(select(Item))}

    known_dishes = {d.slug for d in session.exec(select(Dish))}
    for row in SEED_DISHES:
        if row["slug"] in known_dishes:
            continue
        dish = Dish(
            slug=row["slug"],
            name_en=row["name_en"],
            name_ar=row["name_ar"],
            default_slot=row["slot"],
            full_flame_minutes=row["full_flame"],
            simmer_minutes=row["simmer"],
            burners=row["burners"],
            source=DishSource.SEED,
        )
        session.add(dish)
        session.flush()
        session.add_all(
            DishItem(
                dish_id=dish.id,
                item_id=known_items[item_slug].id,
                qty_per_adult=qty,
            )
            for item_slug, qty in row["items"].items()
            if item_slug in known_items
        )
    session.commit()


def get_household(session: Session) -> Household:
    """The single household. There is no auth yet, so there is one of these."""
    household = session.exec(select(Household)).first()
    if household is None:
        household = Household()
        session.add(household)
        session.commit()
        session.refresh(household)
    return household

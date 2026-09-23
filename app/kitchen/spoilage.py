"""How long food keeps at the temperature it will actually be stored at.

Uses the Q10 rule, the standard simplification of the Arrhenius relationship
used in shelf-life work: raising the temperature by 10 degrees multiplies the
rate of spoilage by Q10, so

    days(T) = days_at_reference * Q10 ** ((reference - T) / 10)

Q10 is about 2 for most foods and higher for microbial spoilage in fresh
produce and meat, which is why a Q10 per item is stored rather than one global
constant.

The practical consequence is the whole reason this exists: bread bought on a
17 degree day in January can reasonably last until tomorrow, and the same bread
on a 32 degree day in August cannot. A shopping list that ignores that either
wastes trips in winter or wastes food in summer.
"""

from dataclasses import dataclass

# The temperature the seeded `keeps_days_at_20c` figures refer to.
REFERENCE_C = 20.0

# Below this many days of remaining life, an item has to be bought on the day it
# is cooked rather than in the weekly shop.
DAILY_THRESHOLD_DAYS = 2.0

# The Q10 curve is only meaningful over the range this app is about: food kept at
# room temperature, with no refrigeration.
#
# The floor is 10 degrees rather than 0 on purpose. Extrapolating the curve down
# to freezing says raw chicken keeps five days, which is true in a fridge and is
# precisely the assumption this app does not make. Clamping there also means a
# genuinely cold snap is treated as a 10 degree day, which understates shelf life
# slightly — an error in the safe direction, towards buying fresher.
MIN_MODELLED_C = 10.0
MAX_MODELLED_C = 45.0


@dataclass(frozen=True)
class Keeping:
    days: float
    at_c: float
    reference_days: float
    buy_daily: bool
    estimated_temperature: bool = False

    @property
    def shortened(self) -> bool:
        """True when heat has cut the item's life compared with the reference."""
        return self.days < self.reference_days - 1e-9


def clamp_temperature(celsius: float) -> float:
    return max(MIN_MODELLED_C, min(MAX_MODELLED_C, celsius))


def keeps_for(
    days_at_reference: float,
    q10: float,
    celsius: float,
    estimated_temperature: bool = False,
) -> Keeping:
    """Effective shelf life in days at a given temperature."""
    if days_at_reference <= 0:
        return Keeping(0.0, celsius, days_at_reference, True, estimated_temperature)

    temperature = clamp_temperature(celsius)
    factor = q10 ** ((REFERENCE_C - temperature) / 10.0) if q10 > 0 else 1.0
    days = round(days_at_reference * factor, 2)

    return Keeping(
        days=days,
        at_c=round(temperature, 1),
        reference_days=days_at_reference,
        buy_daily=days < DAILY_THRESHOLD_DAYS,
        estimated_temperature=estimated_temperature,
    )


def storage_note(name: str, keeping: Keeping) -> str | None:
    """A short, honest line about what the heat means for this item today.

    Returns None when there is nothing worth saying, so a caller can show advice
    only when it changes what someone should do.
    """
    if keeping.days >= 30:
        return None

    if keeping.buy_daily:
        if keeping.shortened:
            return (
                f"At {keeping.at_c:g}°C, {name} keeps about "
                f"{keeping.days:g} day(s) instead of {keeping.reference_days:g}. "
                "Buy it the day you cook it."
            )
        return f"{name} does not keep. Buy it the day you cook it."

    if keeping.shortened:
        return (
            f"At {keeping.at_c:g}°C, {name} keeps about {keeping.days:g} days "
            f"rather than {keeping.reference_days:g}. Keep it in the coolest, "
            "darkest place you have, and use it early in the week."
        )
    return None

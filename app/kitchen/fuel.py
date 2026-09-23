"""What a dish costs in cooking gas.

Where gas is scarce and expensive, the fuel a recipe burns is part of its price.
A pot of beans simmered for an hour and a quarter can cost more in gas than the
beans cost in the shop, and nothing in a normal budgeting app would ever show
that.

The model is deliberately the simple one, because the inputs a household can
actually give are a time and a number of rings:

    kg = burners * (full_flame_hours * FULL_RATE + simmer_hours * SIMMER_RATE)

A single domestic LPG burner runs at roughly 0.25 kg/h at full flame; a low
simmer is far less, taken here as 0.10 kg/h. As a sanity check, a family of five
or six cooking three meals a day is reported to use around 0.3 kg a day, which
is the order of magnitude this produces.

These are averages over unknown stoves, pot sizes and lid discipline. The numbers
are honest to about a third, not to the gram, and are exposed as settings so a
household that knows its own cylinder can calibrate them.
"""

from dataclasses import dataclass

from app.config import settings

# Roles whose long simmer is cut substantially by soaking overnight.
SOAKABLE_ROLES = frozenset({"pulse"})
SOAKING_SAVING = 0.40


@dataclass(frozen=True)
class GasEstimate:
    kg: float
    full_flame_minutes: int
    simmer_minutes: int
    burners: int
    cost: float | None = None
    soaking_saves_kg: float | None = None

    @property
    def minutes(self) -> int:
        return self.full_flame_minutes + self.simmer_minutes


def estimate(
    full_flame_minutes: int,
    simmer_minutes: int,
    burners: int = 1,
    price_per_kg: float | None = None,
    soakable: bool = False,
) -> GasEstimate:
    kg = burners * (
        full_flame_minutes / 60.0 * settings.burner_kg_per_hour
        + simmer_minutes / 60.0 * settings.simmer_kg_per_hour
    )
    kg = round(kg, 4)

    saving = None
    if soakable and simmer_minutes > 0:
        saved = (
            burners
            * (simmer_minutes * SOAKING_SAVING) / 60.0
            * settings.simmer_kg_per_hour
        )
        saving = round(saved, 4)

    return GasEstimate(
        kg=kg,
        full_flame_minutes=full_flame_minutes,
        simmer_minutes=simmer_minutes,
        burners=burners,
        cost=round(kg * price_per_kg, 2) if price_per_kg else None,
        soaking_saves_kg=saving,
    )


def cylinder_days(kg_available: float, kg_per_day: float) -> float | None:
    """How long the gas on hand lasts at the plan's burn rate."""
    if kg_per_day <= 0:
        return None
    return round(kg_available / kg_per_day, 1)

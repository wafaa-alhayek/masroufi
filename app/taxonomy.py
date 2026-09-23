"""Expense categories.

The criteria strings are sent to the classifier verbatim, so they are written as
descriptions of what belongs in the bucket rather than as internal notes.

The taxonomy is deliberately shaped for a Gaza household rather than borrowed
from a Western budgeting app: water and cooking gas are bought by the tanker and
the cylinder, generator fuel and battery charging are a normal monthly line, and
incoming transfers are a primary source of income for many households.
"""

CATEGORIES: dict[str, str] = {
    "groceries": "Food and household groceries: vegetables, flour, bread, oil, tinned goods, a supermarket or grocer",
    "water": "Drinking or domestic water: water tankers, filling stations, water gallons or bottles",
    "cooking_gas": "Cooking gas cylinders or gas refills",
    "power_fuel": "Electricity, generator fuel, diesel, petrol, solar equipment, or paying to charge batteries and phones",
    "transport": "Getting around: shared taxi, private taxi, bus fare, vehicle fuel for transport, car repair",
    "housing": "Rent, shelter, housing repairs, or building materials",
    "health": "Medicine, pharmacy, clinic, hospital, doctor, or medical supplies",
    "communication": "Mobile credit, internet, data bundles, or a telecom operator such as Jawwal, Ooredoo or Paltel",
    "education": "School or university fees, tuition, books, or stationery",
    "clothing": "Clothes, shoes, or fabric",
    "debt_repayment": "Repaying a loan, instalment, or credit owed to a shop or person",
    "transfer_in": "Money arriving: a transfer received, remittance, salary, or aid payment",
    "transfer_out": "Money sent to another person or account",
    "charity": "Zakat, sadaqa, donation, or charitable giving",
    "cash_withdrawal": "Taking cash out at an ATM or branch counter, where what it was spent on is unknown",
    "fees": "Bank charges, commission, service fees, or government fees",
    "other": "A real expense that clearly fits none of the other categories",
}

# Ordinal scale for "could this household realistically spend less here?".
# Index order matters: the classifier returns the index, so 0 is least reducible.
REDUCIBILITY_SCALE: list[str] = [
    "Essential and fixed — cannot be reduced without harm",
    "Essential but the amount or supplier could change",
    "Partly discretionary — could be reduced with effort",
    "Mostly discretionary — could be cut or postponed",
]

CATEGORY_KEYS = tuple(CATEGORIES)


def is_valid_category(key: str) -> bool:
    return key in CATEGORIES

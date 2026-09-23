"""Offline classifier: keyword rules over the note.

This exists so the whole pipeline runs with no API key and no network, and so the
tests are deterministic. It is not a fallback for production — it deliberately
returns low confidence on anything it does not recognise, which routes the
transaction to the user for review.
"""

from app.redact import normalise

from .base import Decision

# Arabic and transliterated keywords sit alongside English because that is what
# the statements actually look like.
_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("water", ("مياه", "ماء", "miyah", "water", "tanker", "غالون")),
    ("cooking_gas", ("غاز", "gas", "cylinder", "اسطوانة")),
    ("power_fuel", ("كهرباء", "بنزين", "سولار", "diesel", "fuel", "petrol", "solar", "شحن")),
    ("communication", ("جوال", "jawwal", "ooredoo", "paltel", "اوريدو", "internet", "نت")),
    ("health", ("صيدلية", "دواء", "pharmacy", "clinic", "مستشفى", "عيادة", "طبيب")),
    ("transport", ("تاكسي", "taxi", "مواصلات", "bus", "باص", "أجرة")),
    ("groceries", ("سوبر", "market", "بقالة", "خضار", "طحين", "خبز", "مخبز", "grocery")),
    ("education", ("مدرسة", "جامعة", "school", "university", "رسوم دراسية", "tuition")),
    ("housing", ("ايجار", "إيجار", "rent")),
    ("charity", ("زكاة", "صدقة", "zakat", "sadaqa", "donation", "تبرع")),
    ("cash_withdrawal", ("atm", "سحب", "withdrawal", "نقدي")),
    ("fees", ("عمولة", "رسوم", "fee", "commission", "charge")),
    ("transfer_in", ("حوالة واردة", "incoming", "راتب", "salary", "deposit")),
    ("transfer_out", ("حوالة صادرة", "outgoing", "تحويل")),
]

_REDUCIBILITY = {
    "water": 0,
    "health": 0,
    "housing": 0,
    "education": 0,
    "cooking_gas": 1,
    "power_fuel": 1,
    "groceries": 1,
    "transport": 2,
    "communication": 2,
    "clothing": 3,
    "charity": 3,
}


class MockClassifier:
    async def classify(self, note: str, amount: float, currency: str) -> Decision:
        key = normalise(note)

        for category, keywords in _RULES:
            if any(word in key for word in keywords):
                return Decision(
                    category=category,
                    confidence=0.88,
                    probabilities={category: 0.88},
                    is_merchant=0.9,
                    reducibility=_REDUCIBILITY.get(category, 2),
                )

        if amount > 0:
            return Decision("transfer_in", 0.6, {"transfer_in": 0.6}, is_merchant=0.2)

        # Unrecognised: low confidence on purpose, so it lands in review.
        return Decision("other", 0.25, {"other": 0.25}, is_merchant=0.3, reducibility=2)

    async def aclose(self) -> None:
        return None

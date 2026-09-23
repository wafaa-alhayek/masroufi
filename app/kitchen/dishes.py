"""Seeded dishes, quantified per adult portion.

    Same caveat as the catalogue, and it matters more here: these quantities were
    set by a developer, not a cook. They are seeded as editable rows, and the
    first thing a household should do is correct them — an over-stated quantity
    here becomes money wasted, and an under-stated one becomes a short meal.

Anything not in this list is generated on request and marked for review.
"""

from app.models import MealSlot

# slug, English, Arabic, slot, {item_slug: grams/ml/pieces per adult}
SEED_DISHES: tuple[tuple, ...] = (
    (
        "mujaddara",
        "Mujaddara",
        "مجدرة",
        MealSlot.LUNCH,
        {"rice": 80, "lentils": 60, "onion": 80, "veg_oil": 15, "cumin": 2, "salt": 3},
    ),
    (
        "maqluba_chicken",
        "Maqluba with chicken",
        "مقلوبة بالدجاج",
        MealSlot.LUNCH,
        {
            "rice": 90,
            "chicken": 200,
            "aubergine": 150,
            "potato": 100,
            "onion": 50,
            "veg_oil": 20,
            "seven_spice": 3,
            "salt": 3,
        },
    ),
    (
        "molokhia",
        "Molokhia",
        "ملوخية",
        MealSlot.LUNCH,
        {
            "molokhia_dry": 30,
            "chicken": 180,
            "rice": 80,
            "garlic": 8,
            "veg_oil": 15,
            "lemon": 30,
            "salt": 3,
        },
    ),
    (
        "fasolia",
        "White bean stew",
        "فاصولياء",
        MealSlot.LUNCH,
        {
            "white_beans": 70,
            "rice": 80,
            "tomato_paste": 25,
            "onion": 50,
            "garlic": 6,
            "veg_oil": 15,
            "salt": 3,
        },
    ),
    (
        "bamia_vegetarian",
        "Lentil and vegetable stew",
        "يخنة عدس وخضار",
        MealSlot.LUNCH,
        {
            "lentils": 70,
            "rice": 80,
            "carrot": 60,
            "potato": 100,
            "onion": 50,
            "tomato_paste": 20,
            "veg_oil": 15,
            "salt": 3,
        },
    ),
    (
        "koosa_mahshi",
        "Stuffed courgette",
        "كوسا محشي",
        MealSlot.LUNCH,
        {
            "courgette": 250,
            "rice": 60,
            "beef": 80,
            "tomato_paste": 20,
            "seven_spice": 3,
            "salt": 3,
        },
    ),
    (
        "ful",
        "Ful medames",
        "فول مدمس",
        MealSlot.BREAKFAST,
        {"fava_beans": 70, "olive_oil": 15, "lemon": 20, "cumin": 2, "bread": 100, "salt": 2},
    ),
    (
        "hummus_breakfast",
        "Hummus",
        "حمص",
        MealSlot.BREAKFAST,
        {"chickpeas": 70, "tahini": 25, "lemon": 20, "garlic": 4, "bread": 100, "salt": 2},
    ),
    (
        "zaatar_bread",
        "Bread with zaatar and oil",
        "خبز بالزعتر والزيت",
        MealSlot.BREAKFAST,
        {"bread": 120, "zaatar": 15, "olive_oil": 15},
    ),
    (
        "eggs_tomato",
        "Eggs with tomato",
        "بيض بالبندورة",
        MealSlot.BREAKFAST,
        {"eggs": 1.5, "tomato": 100, "onion": 30, "veg_oil": 10, "bread": 100, "salt": 2},
    ),
    (
        "shakshuka",
        "Shakshuka",
        "شكشوكة",
        MealSlot.DINNER,
        {
            "eggs": 2,
            "tomato": 150,
            "pepper": 60,
            "onion": 40,
            "veg_oil": 12,
            "bread": 100,
            "salt": 2,
        },
    ),
    (
        "lentil_soup",
        "Lentil soup",
        "شوربة عدس",
        MealSlot.DINNER,
        {"lentils": 70, "onion": 40, "carrot": 40, "veg_oil": 10, "cumin": 2, "salt": 3},
    ),
)

"""Seeded dishes: what they need, and what they cost to cook.

    Same caveat as the catalogue, and it matters more here: these quantities and
    cooking times were set by a developer, not a cook. They are seeded as
    editable rows, and the first thing a household should do is correct them —
    an over-stated quantity here becomes money wasted, and an under-stated one
    becomes a short meal.

`full_flame` and `simmer` are minutes, and `burners` is how many rings are lit
at once. Together they are what a dish costs in gas, which where fuel is scarce
is part of its real price: a pot of beans simmered for an hour and a quarter can
cost more to cook than the beans cost to buy.

Anything not in this list is generated on request and marked for review.
"""

from app.models import MealSlot

SEED_DISHES: tuple[dict, ...] = (
    {
        "slug": "mujaddara",
        "name_en": "Mujaddara",
        "name_ar": "مجدرة",
        "slot": MealSlot.LUNCH,
        "full_flame": 12,
        "simmer": 35,
        "burners": 1,
        "items": {"rice": 80, "lentils": 60, "onion": 80, "veg_oil": 15, "cumin": 2, "salt": 3},
    },
    {
        "slug": "maqluba_chicken",
        "name_en": "Maqluba with chicken",
        "name_ar": "مقلوبة بالدجاج",
        "slot": MealSlot.LUNCH,
        "full_flame": 20,
        "simmer": 45,
        "burners": 2,
        "items": {
            "rice": 90,
            "chicken": 200,
            "aubergine": 150,
            "potato": 100,
            "onion": 50,
            "veg_oil": 20,
            "seven_spice": 3,
            "salt": 3,
        },
    },
    {
        "slug": "molokhia",
        "name_en": "Molokhia",
        "name_ar": "ملوخية",
        "slot": MealSlot.LUNCH,
        "full_flame": 15,
        "simmer": 40,
        "burners": 2,
        "items": {
            "molokhia_dry": 30,
            "chicken": 180,
            "rice": 80,
            "garlic": 8,
            "veg_oil": 15,
            "lemon": 30,
            "salt": 3,
        },
    },
    {
        "slug": "fasolia",
        "name_en": "White bean stew",
        "name_ar": "فاصولياء",
        "slot": MealSlot.LUNCH,
        # The expensive one to cook. Soaking the beans overnight cuts the simmer
        # substantially, which the gas estimate calls out.
        "full_flame": 10,
        "simmer": 75,
        "burners": 1,
        "items": {
            "white_beans": 70,
            "rice": 80,
            "tomato_paste": 25,
            "onion": 50,
            "garlic": 6,
            "veg_oil": 15,
            "salt": 3,
        },
    },
    {
        "slug": "bamia_vegetarian",
        "name_en": "Lentil and vegetable stew",
        "name_ar": "يخنة عدس وخضار",
        "slot": MealSlot.LUNCH,
        "full_flame": 10,
        "simmer": 40,
        "burners": 1,
        "items": {
            "lentils": 70,
            "rice": 80,
            "carrot": 60,
            "potato": 100,
            "onion": 50,
            "tomato_paste": 20,
            "veg_oil": 15,
            "salt": 3,
        },
    },
    {
        "slug": "koosa_mahshi",
        "name_en": "Stuffed courgette",
        "name_ar": "كوسا محشي",
        "slot": MealSlot.LUNCH,
        "full_flame": 15,
        "simmer": 50,
        "burners": 1,
        "items": {
            "courgette": 250,
            "rice": 60,
            "beef": 80,
            "tomato_paste": 20,
            "seven_spice": 3,
            "salt": 3,
        },
    },
    {
        "slug": "ful",
        "name_en": "Ful medames",
        "name_ar": "فول مدمس",
        "slot": MealSlot.BREAKFAST,
        "full_flame": 5,
        "simmer": 20,
        "burners": 1,
        "items": {
            "fava_beans": 70,
            "olive_oil": 15,
            "lemon": 20,
            "cumin": 2,
            "bread": 100,
            "salt": 2,
        },
    },
    {
        "slug": "hummus_breakfast",
        "name_en": "Hummus",
        "name_ar": "حمص",
        "slot": MealSlot.BREAKFAST,
        "full_flame": 10,
        "simmer": 60,
        "burners": 1,
        "items": {
            "chickpeas": 70,
            "tahini": 25,
            "lemon": 20,
            "garlic": 4,
            "bread": 100,
            "salt": 2,
        },
    },
    {
        "slug": "zaatar_bread",
        "name_en": "Bread with zaatar and oil",
        "name_ar": "خبز بالزعتر والزيت",
        "slot": MealSlot.BREAKFAST,
        # No cooking at all, so no gas. Worth having in the library for exactly
        # that reason when fuel is short.
        "full_flame": 0,
        "simmer": 0,
        "burners": 0,
        "items": {"bread": 120, "zaatar": 15, "olive_oil": 15},
    },
    {
        "slug": "eggs_tomato",
        "name_en": "Eggs with tomato",
        "name_ar": "بيض بالبندورة",
        "slot": MealSlot.BREAKFAST,
        "full_flame": 10,
        "simmer": 0,
        "burners": 1,
        "items": {
            "eggs": 1.5,
            "tomato": 100,
            "onion": 30,
            "veg_oil": 10,
            "bread": 100,
            "salt": 2,
        },
    },
    {
        "slug": "shakshuka",
        "name_en": "Shakshuka",
        "name_ar": "شكشوكة",
        "slot": MealSlot.DINNER,
        "full_flame": 8,
        "simmer": 12,
        "burners": 1,
        "items": {
            "eggs": 2,
            "tomato": 150,
            "pepper": 60,
            "onion": 40,
            "veg_oil": 12,
            "bread": 100,
            "salt": 2,
        },
    },
    {
        "slug": "lentil_soup",
        "name_en": "Lentil soup",
        "name_ar": "شوربة عدس",
        "slot": MealSlot.DINNER,
        "full_flame": 10,
        "simmer": 30,
        "burners": 1,
        "items": {"lentils": 70, "onion": 40, "carrot": 40, "veg_oil": 10, "cumin": 2, "salt": 3},
    },
)

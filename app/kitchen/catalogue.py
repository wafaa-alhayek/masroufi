"""The seeded grocery catalogue.

Shelf life is the important column, because it decides whether an item goes on
the daily list or the weekly one. It is judged **without refrigeration**: eggs
and root vegetables keep a few days on a shelf, meat and leafy greens do not keep
at all.

    These quantities and shelf lives are a starting point set by a developer, not
    by someone who shops in Gaza. They are seeded as editable rows precisely so
    that a household — or you — can correct them, and `purchase_step` in
    particular should be checked against how these things are actually sold.
"""

from app.models import Item, ItemRole, ShelfLife, Unit

G, ML, PIECE, BUNCH = Unit.GRAM, Unit.ML, Unit.PIECE, Unit.BUNCH
PERISHABLE, KEEPS, STABLE = ShelfLife.PERISHABLE, ShelfLife.KEEPS_DAYS, ShelfLife.STABLE

# slug, English, Arabic, unit, shelf life, role, purchase step,
#   days it keeps at 20C without refrigeration, Q10
CATALOGUE: tuple[tuple, ...] = (
    # Grains and flour
    ("rice", "Rice", "أرز", G, STABLE, ItemRole.GRAIN, 250, 540, 1.6),
    ("flour", "Wheat flour", "طحين", G, STABLE, ItemRole.GRAIN, 500, 270, 1.8),
    ("freekeh", "Freekeh", "فريكة", G, STABLE, ItemRole.GRAIN, 250, 540, 1.6),
    ("bulgur", "Bulgur", "برغل", G, STABLE, ItemRole.GRAIN, 250, 540, 1.6),
    ("pasta", "Pasta", "معكرونة", G, STABLE, ItemRole.GRAIN, 250, 540, 1.6),
    # Pulses
    ("lentils", "Lentils", "عدس", G, STABLE, ItemRole.PULSE, 250, 540, 1.6),
    ("chickpeas", "Chickpeas", "حمص", G, STABLE, ItemRole.PULSE, 250, 540, 1.6),
    ("white_beans", "White beans", "فاصولياء بيضاء", G, STABLE, ItemRole.PULSE, 250, 540, 1.6),
    ("fava_beans", "Fava beans", "فول", G, STABLE, ItemRole.PULSE, 250, 540, 1.6),
    # Protein — none of this keeps without refrigeration
    ("chicken", "Chicken", "دجاج", G, PERISHABLE, ItemRole.POULTRY, 250, 0.4, 3.5),
    ("beef", "Beef", "لحم بقري", G, PERISHABLE, ItemRole.MEAT, 250, 0.5, 3.5),
    ("lamb", "Lamb", "لحم غنم", G, PERISHABLE, ItemRole.MEAT, 250, 0.5, 3.5),
    ("fish", "Fish", "سمك", G, PERISHABLE, ItemRole.FISH, 250, 0.3, 4.0),
    ("eggs", "Eggs", "بيض", PIECE, KEEPS, ItemRole.EGG, 1, 10, 2.5),
    ("yoghurt", "Yoghurt", "لبن", G, PERISHABLE, ItemRole.DAIRY, 250, 0.5, 3.5),
    ("white_cheese", "White cheese", "جبنة بيضاء", G, PERISHABLE, ItemRole.DAIRY, 250, 1.0, 3.0),
    ("labneh", "Labneh", "لبنة", G, PERISHABLE, ItemRole.DAIRY, 250, 0.8, 3.0),
    # Vegetables
    ("tomato", "Tomatoes", "بندورة", G, PERISHABLE, ItemRole.VEG_FRUIT, 250, 4, 2.5),
    ("cucumber", "Cucumbers", "خيار", G, PERISHABLE, ItemRole.VEG_FRUIT, 250, 3, 2.5),
    ("aubergine", "Aubergine", "باذنجان", G, KEEPS, ItemRole.VEG_FRUIT, 250, 5, 2.5),
    ("courgette", "Courgette", "كوسا", G, PERISHABLE, ItemRole.VEG_FRUIT, 250, 3, 2.5),
    ("pepper", "Green pepper", "فلفل أخضر", G, PERISHABLE, ItemRole.VEG_FRUIT, 250, 4, 2.5),
    ("onion", "Onions", "بصل", G, STABLE, ItemRole.VEG_ROOT, 250, 45, 2.0),
    ("garlic", "Garlic", "ثوم", G, STABLE, ItemRole.VEG_ROOT, 50, 90, 1.8),
    ("potato", "Potatoes", "بطاطا", G, KEEPS, ItemRole.VEG_ROOT, 500, 21, 2.0),
    ("carrot", "Carrots", "جزر", G, KEEPS, ItemRole.VEG_ROOT, 250, 10, 2.2),
    ("molokhia_dry", "Dried molokhia", "ملوخية ناشفة", G, STABLE, ItemRole.VEG_LEAF, 100, 360, 1.6),
    ("spinach", "Spinach", "سبانخ", BUNCH, PERISHABLE, ItemRole.VEG_LEAF, 1, 1.5, 3.0),
    ("parsley", "Parsley", "بقدونس", BUNCH, PERISHABLE, ItemRole.VEG_LEAF, 1, 2, 3.0),
    ("mint", "Mint", "نعنع", BUNCH, PERISHABLE, ItemRole.VEG_LEAF, 1, 2, 3.0),
    ("lemon", "Lemons", "ليمون", G, KEEPS, ItemRole.FRUIT, 250, 12, 2.2),
    # Bread
    ("bread", "Bread", "خبز", G, PERISHABLE, ItemRole.BREAD, 500, 3, 2.5),
    # Fats, pastes, seasoning
    ("olive_oil", "Olive oil", "زيت زيتون", ML, STABLE, ItemRole.FAT, 250, 540, 1.5),
    ("veg_oil", "Vegetable oil", "زيت نباتي", ML, STABLE, ItemRole.FAT, 500, 360, 1.5),
    ("tahini", "Tahini", "طحينة", G, STABLE, ItemRole.PASTE, 250, 270, 1.6),
    ("tomato_paste", "Tomato paste", "معجون بندورة", G, STABLE, ItemRole.PASTE, 100, 270, 1.8),
    ("salt", "Salt", "ملح", G, STABLE, ItemRole.SPICE, 250, 1800, 1.2),
    ("cumin", "Cumin", "كمون", G, STABLE, ItemRole.SPICE, 50, 540, 1.5),
    ("black_pepper", "Black pepper", "فلفل أسود", G, STABLE, ItemRole.SPICE, 50, 540, 1.5),
    ("seven_spice", "Seven spice", "سبع بهارات", G, STABLE, ItemRole.SPICE, 50, 540, 1.5),
    ("zaatar", "Zaatar", "زعتر", G, STABLE, ItemRole.SPICE, 100, 270, 1.6),
    ("chilli", "Chilli", "شطة", G, STABLE, ItemRole.SPICE, 50, 540, 1.5),
    # Store cupboard
    ("sugar", "Sugar", "سكر", G, STABLE, ItemRole.SWEETENER, 500, 1800, 1.2),
    ("tea", "Tea", "شاي", G, STABLE, ItemRole.DRINK, 100, 540, 1.5),
    ("coffee", "Coffee", "قهوة", G, STABLE, ItemRole.DRINK, 100, 360, 1.6),
    # Parcel and tinned goods. Core aid-parcel contents, and without them a
    # parcel cannot be recorded at all. Role and shelf life are independent here
    # in a way they are not elsewhere: tinned sardines are FISH and keep two
    # years, powdered milk is DAIRY and keeps eighteen months.
    ("canned_tuna", "Tinned tuna", "تونة معلبة", G, STABLE, ItemRole.FISH, 150, 720, 1.3),
    ("canned_sardines", "Tinned sardines", "سردين معلب", G, STABLE, ItemRole.FISH, 125, 720, 1.3),
    ("canned_meat", "Tinned meat", "لحمة معلبة", G, STABLE, ItemRole.MEAT, 200, 720, 1.3),
    ("canned_fava", "Tinned fava beans", "فول معلب", G, STABLE, ItemRole.PULSE, 400, 540, 1.4),
    ("canned_chickpeas", "Tinned chickpeas", "حمص معلب", G, STABLE, ItemRole.PULSE, 400, 540, 1.4),
    ("canned_white_beans", "Tinned white beans", "فاصولياء معلبة", G, STABLE, ItemRole.PULSE, 400, 540, 1.4),
    ("canned_tomato", "Tinned tomatoes", "بندورة معلبة", G, STABLE, ItemRole.VEG_FRUIT, 400, 720, 1.4),
    ("milk_powder", "Powdered milk", "حليب بودرة", G, STABLE, ItemRole.DAIRY, 400, 540, 1.5),
    ("halawa", "Halawa", "حلاوة طحينية", G, STABLE, ItemRole.PASTE, 250, 360, 1.6),
    ("dates", "Dates", "تمر", G, STABLE, ItemRole.FRUIT, 250, 270, 1.8),
    # Named in the dish drafting notes.
    ("sumac", "Sumac", "سماق", G, STABLE, ItemRole.SPICE, 50, 540, 1.5),
    ("chard", "Chard", "سلق", BUNCH, PERISHABLE, ItemRole.VEG_LEAF, 1, 2, 3.0),
    ("maftoul", "Maftoul", "مفتول", G, STABLE, ItemRole.GRAIN, 500, 540, 1.6),
    # Baking bread from parcel flour is common enough to plan for.
    ("yeast", "Yeast", "خميرة", G, STABLE, ItemRole.OTHER, 50, 360, 1.8),
)


def seed_items() -> list[Item]:
    return [
        Item(
            slug=slug,
            name_en=name_en,
            name_ar=name_ar,
            unit=unit,
            shelf_life=shelf_life,
            role=role,
            purchase_step=step,
            keeps_days_at_20c=keeps,
            q10=q10,
        )
        for slug, name_en, name_ar, unit, shelf_life, role, step, keeps, q10 in CATALOGUE
    ]

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

# slug, English, Arabic, unit, shelf life, role, purchase step
CATALOGUE: tuple[tuple, ...] = (
    # Grains and flour
    ("rice", "Rice", "أرز", G, STABLE, ItemRole.GRAIN, 250),
    ("flour", "Wheat flour", "طحين", G, STABLE, ItemRole.GRAIN, 500),
    ("freekeh", "Freekeh", "فريكة", G, STABLE, ItemRole.GRAIN, 250),
    ("bulgur", "Bulgur", "برغل", G, STABLE, ItemRole.GRAIN, 250),
    ("pasta", "Pasta", "معكرونة", G, STABLE, ItemRole.GRAIN, 250),
    # Pulses
    ("lentils", "Lentils", "عدس", G, STABLE, ItemRole.PULSE, 250),
    ("chickpeas", "Chickpeas", "حمص", G, STABLE, ItemRole.PULSE, 250),
    ("white_beans", "White beans", "فاصولياء بيضاء", G, STABLE, ItemRole.PULSE, 250),
    ("fava_beans", "Fava beans", "فول", G, STABLE, ItemRole.PULSE, 250),
    # Protein — none of this keeps without refrigeration
    ("chicken", "Chicken", "دجاج", G, PERISHABLE, ItemRole.POULTRY, 250),
    ("beef", "Beef", "لحم بقري", G, PERISHABLE, ItemRole.MEAT, 250),
    ("lamb", "Lamb", "لحم غنم", G, PERISHABLE, ItemRole.MEAT, 250),
    ("fish", "Fish", "سمك", G, PERISHABLE, ItemRole.FISH, 250),
    ("eggs", "Eggs", "بيض", PIECE, KEEPS, ItemRole.EGG, 1),
    ("yoghurt", "Yoghurt", "لبن", G, PERISHABLE, ItemRole.DAIRY, 250),
    ("white_cheese", "White cheese", "جبنة بيضاء", G, PERISHABLE, ItemRole.DAIRY, 250),
    ("labneh", "Labneh", "لبنة", G, PERISHABLE, ItemRole.DAIRY, 250),
    # Vegetables
    ("tomato", "Tomatoes", "بندورة", G, PERISHABLE, ItemRole.VEG_FRUIT, 250),
    ("cucumber", "Cucumbers", "خيار", G, PERISHABLE, ItemRole.VEG_FRUIT, 250),
    ("aubergine", "Aubergine", "باذنجان", G, KEEPS, ItemRole.VEG_FRUIT, 250),
    ("courgette", "Courgette", "كوسا", G, PERISHABLE, ItemRole.VEG_FRUIT, 250),
    ("pepper", "Green pepper", "فلفل أخضر", G, PERISHABLE, ItemRole.VEG_FRUIT, 250),
    ("onion", "Onions", "بصل", G, STABLE, ItemRole.VEG_ROOT, 250),
    ("garlic", "Garlic", "ثوم", G, STABLE, ItemRole.VEG_ROOT, 50),
    ("potato", "Potatoes", "بطاطا", G, KEEPS, ItemRole.VEG_ROOT, 500),
    ("carrot", "Carrots", "جزر", G, KEEPS, ItemRole.VEG_ROOT, 250),
    ("molokhia_dry", "Dried molokhia", "ملوخية ناشفة", G, STABLE, ItemRole.VEG_LEAF, 100),
    ("spinach", "Spinach", "سبانخ", BUNCH, PERISHABLE, ItemRole.VEG_LEAF, 1),
    ("parsley", "Parsley", "بقدونس", BUNCH, PERISHABLE, ItemRole.VEG_LEAF, 1),
    ("mint", "Mint", "نعنع", BUNCH, PERISHABLE, ItemRole.VEG_LEAF, 1),
    ("lemon", "Lemons", "ليمون", G, KEEPS, ItemRole.FRUIT, 250),
    # Bread
    ("bread", "Bread", "خبز", G, PERISHABLE, ItemRole.BREAD, 500),
    # Fats, pastes, seasoning
    ("olive_oil", "Olive oil", "زيت زيتون", ML, STABLE, ItemRole.FAT, 250),
    ("veg_oil", "Vegetable oil", "زيت نباتي", ML, STABLE, ItemRole.FAT, 500),
    ("tahini", "Tahini", "طحينة", G, STABLE, ItemRole.PASTE, 250),
    ("tomato_paste", "Tomato paste", "معجون بندورة", G, STABLE, ItemRole.PASTE, 100),
    ("salt", "Salt", "ملح", G, STABLE, ItemRole.SPICE, 250),
    ("cumin", "Cumin", "كمون", G, STABLE, ItemRole.SPICE, 50),
    ("black_pepper", "Black pepper", "فلفل أسود", G, STABLE, ItemRole.SPICE, 50),
    ("seven_spice", "Seven spice", "سبع بهارات", G, STABLE, ItemRole.SPICE, 50),
    ("zaatar", "Zaatar", "زعتر", G, STABLE, ItemRole.SPICE, 100),
    ("chilli", "Chilli", "شطة", G, STABLE, ItemRole.SPICE, 50),
    # Store cupboard
    ("sugar", "Sugar", "سكر", G, STABLE, ItemRole.SWEETENER, 500),
    ("tea", "Tea", "شاي", G, STABLE, ItemRole.DRINK, 100),
    ("coffee", "Coffee", "قهوة", G, STABLE, ItemRole.DRINK, 100),
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
        )
        for slug, name_en, name_ar, unit, shelf_life, role, step in CATALOGUE
    ]

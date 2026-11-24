"""Market prices from a public Telegram channel's export.

`أسعار السوق - غزة` (t.me/asarsook) posts what things cost in Gaza's markets,
most days since November 2024. Telegram's own "Export chat history" produces an
HTML file; this turns its price lines into rows the app can load as dated
observations.

The lines are written by hand for people, not machines, so parsing is deliberately
conservative. A line becomes a usable price only when all three are clear:

  * **the price**, a number marked as shekels (₪, ش, شيكل) — or a range of them;
  * **the item**, matched to exactly one catalogue slug by its Arabic name;
  * **the quantity**, stated (a kilo, a وقية, a litre, 700 g, a head of garlic),
    or, for loose produce, grains, pulses and sugar only, the kilo it is
    customarily quoted by — and then marked `unit_inferred`.

Everything else is still written out with a `status` saying why it was not used,
so the rules can be loosened by someone who checks what they would let in.

    python -m app.importers.telegram_prices messages.html data/prices/asarsook.csv

The raw export is never committed. The output keeps each line's text and message
number, so any figure can be traced back to t.me/asarsook/<message_id>.
"""

from __future__ import annotations

import csv
import html
import re
import sys
from dataclasses import asdict, dataclass, fields
from datetime import date
from pathlib import Path

CHANNEL = "asarsook"

# A وقية in Gaza's markets is taken as a quarter kilo. The channel never defines
# it; the raw unit is kept on every row so prices can be recomputed if this is
# wrong.
WIQIYA_G = 250.0

# --- text normalisation ----------------------------------------------------

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_DIACRITICS = re.compile(r"[ً-ْـ]")  # harakat and tatweel


def normalise(text: str) -> str:
    """Fold the spellings that vary from post to post onto one form."""
    text = _DIACRITICS.sub("", text.translate(_ARABIC_DIGITS))
    text = re.sub("[أإآ]", "ا", text)
    text = text.replace("ى", "ي").replace("ة", "ه").replace("ڪ", "ك")
    return text


def _words(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[؀-ۿ]+", normalise(text)):
        if w.startswith("ال") and len(w) > 3:
            w = w[2:]
        out.append(w)
    return out


# --- item matching ---------------------------------------------------------

# Words the channel uses that the catalogues do not list as names.
CHANNEL_ALIASES: dict[str, str] = {
    "سيرج": "veg_oil",
    "زيت نباتي": "veg_oil",
    "زيت قلي": "veg_oil",
    "رز": "rice",
    "ارز": "rice",
    "طماطم": "tomato",
    "بندوره": "tomato",
    # In Gaza فلفل أخضر is the hot green chilli, sold by the وقية at several
    # times the price of a sweet pepper (فليفلة). The hand-written catalogue's
    # `pepper` is the sweet one, whatever its Arabic name says.
    "فليفله": "pepper",
    "فلفل حلو": "pepper",
    "فلفل رومي": "pepper",
    "فلفل اخضر": "hot_green_chilli",
    "فلفل بلدي": "hot_green_chilli",
    "فلفل سلطان": "hot_green_chilli",
    "فلفل حار": "hot_green_chilli",
    "بتنجان": "aubergine",
    "طحينيه": "tahini",
    "بيضه": "eggs",
    "بيض": "eggs",
    "ثومه": "garlic",
    "طحين": "flour",
    "عدس": "lentils",
    "عدس مجروش": "lentils",
    "عدس احمر": "lentils",
    "عدس حب": "lentils",
    "حمص حب": "chickpeas",
    "فول ناشف": "fava_beans",
    "ملوخيه ناشفه": "molokhia_dry",
    # In Gaza plain لحمة is beef unless the line names another animal; the
    # longer names below (lamb, chicken, tins) win because they match more words.
    "لحم": "beef",
    "لحمه": "beef",
    "لحم موزه": "beef",  # shank, a cut of beef, not bananas
    "لحمه موزه": "beef",
    "لحم عجل": "beef",
    "لحمه عجل": "beef",
    "لحم ضان": "lamb",
    "لحم خروف": "lamb",
    "لحمه خروف": "lamb",
    # كفتة is how the channel sells raw ground meat, not the frozen kebab item.
    "كفته": "minced_meat",
    "لحمه اكسترا": "canned_meat",
    "علبه لحمه": "canned_meat",
}

# Qualifiers that make a line about something other than the base item.
_CANNED = re.compile(r"علب|معلب")

# Phrases that contain an item's name but are not that item: citric acid is
# not lemons, cooked okra is a
# dish, and Deir al-Balah is a town, not dates.
_NOT_THE_ITEM = re.compile(
    r"حمض|ملح ليمون|شبره|مطبوخ|نقل|توصيل|اجره|مواصلات"
)
_PLACES = re.compile(
    r"دير البلح|خانيونس|خان يونس|النصيرات|الزوايده|المواصي|رفح|جباليا|بيت لاهيا|الشجاعيه|الرمال"
)


# How sure a name is: a channel alias is a deliberate decision, an item's own
# name beats one of its aliases. `جزر` is carrot's name and also an alias of
# frozen carrots; the name wins.
BY_CHANNEL, BY_NAME, BY_ALIAS = 0, 1, 2


def _alias_index(items: dict[str, dict]) -> dict[tuple[str, ...], dict[str, int]]:
    index: dict[tuple[str, ...], dict[str, int]] = {}

    def add(name: str, slug: str, rank: int) -> None:
        key = tuple(_words(name))
        if key:
            slugs = index.setdefault(key, {})
            slugs[slug] = min(rank, slugs.get(slug, rank))

    for slug, info in items.items():
        add(info["name_ar"], slug, BY_NAME)
        for alias in info.get("aliases_ar", []):
            add(alias, slug, BY_ALIAS)
    for alias, slug in CHANNEL_ALIASES.items():
        if slug in items:
            index[tuple(_words(alias))] = {slug: BY_CHANNEL}
    return index


def match_item(text: str, items: dict[str, dict], index=None) -> tuple[str | None, str]:
    """The one catalogue slug a line names, or None with the reason."""
    index = index or _alias_index(items)
    text = normalise(text)
    if _NOT_THE_ITEM.search(text):
        return None, "not_the_item"
    words = _words(_PLACES.sub(" ", text))
    best_len, found = 0, {}
    for i in range(len(words)):
        for j in range(len(words), i, -1):
            slugs = index.get(tuple(words[i:j]))
            if not slugs:
                continue
            n = j - i
            if n > best_len:
                best_len, found = n, dict(slugs)
            elif n == best_len:
                for slug, rank in slugs.items():
                    found[slug] = min(rank, found.get(slug, rank))
            break
    if not found:
        return None, "unmatched_item"
    top = min(found.values())
    winners = [slug for slug, rank in found.items() if rank == top]
    if len(winners) > 1:
        return None, "ambiguous_item"
    slug = winners[0]
    if _CANNED.search(normalise(text)) and not slug.startswith("canned_"):
        # Tinned beef is catalogued as canned_meat; there is no canned_beef.
        canned = "canned_meat" if slug == "beef" else f"canned_{slug}"
        if canned in items:
            return canned, ""
        return None, "canned_form_unknown"
    return slug, ""


# --- price and quantity ----------------------------------------------------

_NUM = r"\d+(?:[.,]\d+)?"
_CURRENCY = r"(?:₪|شيكل|شيقل|ش(?![؀-ۿ]))"
_PRICE = re.compile(
    rf"(?P<low>{_NUM})\s*{_CURRENCY}?\s*(?:[-–—_/]|الى|إلى)\s*(?P<high>{_NUM})\s*{_CURRENCY}"
    rf"|(?P<one>{_NUM})\s*{_CURRENCY}"
)

# unit word -> (kind, multiplier into that kind's base). Bases: g, ml, piece, bunch.
_UNITS: list[tuple[re.Pattern, str, float]] = [
    (re.compile(r"(?:نص|نصف)\s*(?:ال)?(?:كيلو|كغم?|كجم)"), "g", 500.0),
    (re.compile(rf"(?P<n>{_NUM})?\s*(?:ال)?(?:كيلو|كيلوغرام|كغم?|كجم|ك(?![؀-ۿ]))"), "g", 1000.0),
    (re.compile(rf"(?P<n>{_NUM})?\s*(?:ال)?(?:وقيه|وقية|اوقيه|أوقية)"), "g", WIQIYA_G),
    (re.compile(rf"(?P<n>{_NUM})\s*(?:غرام|جرام|غم|جم|غ(?![؀-ۿ]))"), "g", 1.0),
    (re.compile(rf"(?P<n>{_NUM})?\s*(?:ال)?(?:لتر|ليتر)"), "ml", 1000.0),
    (re.compile(rf"(?P<n>{_NUM})\s*(?:مل|ملل)(?![؀-ۿ])"), "ml", 1.0),
    (re.compile(r"(?:طبق|كرتونه|كرتونة)\s*(?:ال)?بيض"), "piece", 30.0),
    (re.compile(rf"(?P<n>{_NUM})?\s*(?:ال)?(?:راس|رأس|حبه|حبة)(?![؀-ۿ])"), "piece", 1.0),
    (re.compile(rf"(?P<n>{_NUM})?\s*(?:ال)?(?:ربطه|ربطة|حزمه|حزمة|ضمه|ضمة)"), "bunch", 1.0),
]
_TIN = re.compile(r"علب")
_SACK = re.compile(r"كيس|شوال")

# What a unitless price is customarily quoted by. Produce, grains, pulses and
# sugar are sold loose by the kilo, and so is fresh meat and fish; cooking oil by
# the litre. Anything else unitless — tins, packets, a sack of flour — is left
# out rather than guessed, because a per-piece price read as per-kilo is wrong
# by an order of magnitude.
_LOOSE_BY_KILO = {"veg_fruit", "veg_root", "veg_leaf", "fruit", "grain", "pulse", "sweetener"}
_FRESH_BY_KILO = {"meat", "poultry", "fish"}

_SKIP = re.compile(r"دولار|دينار|يورو|صرف|عمول|سحب|نسبه|نسبة|%|ضبط|مخالف|\d{7,}")
_PROSE = re.compile(r"(?<![\u0600-\u06FF])(?:اللي|الذي|التي|كان|يشتري|يشتريه|خلافا)(?![\u0600-\u06FF])|وصل حق")
_D = "[0-9\u0660-\u0669\u06F0-\u06F9]"
# Seven or more digits in a row, or a mobile number written in groups (059 7…).
_LONG_NUMBER = re.compile(rf"{_D}{{7,}}|(?:\+?970|0)5{_D}(?:[\s-]?{_D}){{7}}")
_PAY_APP = re.compile(r"تطبيق|بنكي|محفظ")
_PAY_CASH = re.compile(r"نقد|كاش|كش(?![؀-ۿ])")


@dataclass
class PriceLine:
    message_id: int
    posted_on: str
    region: str
    line: str
    status: str = ""
    item_slug: str = ""
    price_low: float = 0.0
    price_high: float = 0.0
    quantity: float = 0.0
    unit: str = ""  # g, ml, piece, bunch — the base the quantity is in
    raw_unit: str = ""  # what the line said: kilo, wiqiya, litre, tin, ...
    unit_inferred: bool = False
    payment: str = ""  # cash, app, or blank when the line does not say
    per_unit: float = 0.0  # midpoint price for one g / ml / piece / bunch

    @property
    def usable(self) -> bool:
        return self.status == "ok"


_NUMBER_WORDS = {
    "واحد": "1", "اثنين": "2", "ثلاث": "3", "ثلاثه": "3", "اربع": "4", "اربعه": "4",
    "خمس": "5", "خمسه": "5", "سته": "6", "عشر": "10", "عشره": "10",
}
_NUMBER_WORD = re.compile(r"(?<![\u0600-\u06FF])(" + "|".join(_NUMBER_WORDS) + r")(?![\u0600-\u06FF])")


def _quantity(
    text: str, unit: str, role: str, shelf_life: str = ""
) -> tuple[float, str, str, bool, str]:
    """(quantity, base unit, raw unit, inferred?, problem)."""
    t = _NUMBER_WORD.sub(lambda m: _NUMBER_WORDS[m.group(1)], normalise(text))
    for pattern, kind, mult in _UNITS:
        m = pattern.search(t)
        if not m:
            continue
        n = m.groupdict().get("n")
        qty = (float(n.replace(",", ".")) if n else 1.0) * mult
        raw = m.group(0).strip()
        if kind != unit:
            return 0, kind, raw, False, "unit_mismatch"
        return qty, kind, raw, False, ""
    if _TIN.search(t):
        return 0, "", "tin", False, "tin_without_weight"
    if _SACK.search(t):
        return 0, "", "sack", False, "sack_without_weight"
    if unit == "g" and (
        role in _LOOSE_BY_KILO or (role in _FRESH_BY_KILO and shelf_life == "perishable")
    ):
        return 1000.0, "g", "kilo", True, ""
    if unit == "ml" and role == "fat":
        return 1000.0, "ml", "litre", True, ""
    return 0, "", "", False, "no_quantity"


def parse_line(
    text: str,
    items: dict[str, dict],
    *,
    message_id: int = 0,
    posted_on: str = "",
    region: str = "",
    index=None,
) -> PriceLine | None:
    """One line of a post. None when the line carries no shekel price at all."""
    clean = normalise(text).replace("*", " ").strip(" •-▪️👈🔹🔸:")
    m = _PRICE.search(clean)
    if not m:
        return None
    # Lines are kept for auditing; a phone or account number in one is not.
    kept = _LONG_NUMBER.sub("[NUM]", text.strip())
    row = PriceLine(message_id=message_id, posted_on=posted_on, region=region, line=kept)
    if _SKIP.search(clean):
        row.status = "not_a_price"
        return row

    if m.group("one"):
        low = high = float(m.group("one").replace(",", "."))
    else:
        low, high = sorted(float(m.group(g).replace(",", ".")) for g in ("low", "high"))
    row.price_low, row.price_high = low, high
    if low <= 0:
        row.status = "no_price"
        return row

    # The item is named in the text outside the price itself.
    named = (clean[: m.start()] + " " + clean[m.end():]).strip()
    # A price list line is a few words. A sentence that happens to mention a
    # price is news ("whoever used to buy sugar at 500 shekels...").
    if len(_words(clean[: m.start()])) > 7 or _PROSE.search(clean):
        row.status = "prose"
        return row
    slug, problem = match_item(named, items, index)
    if not slug:
        row.status = problem
        return row
    row.item_slug = slug

    info = items[slug]
    qty, unit, raw, inferred, problem = _quantity(
        named, info["unit"], info["role"], info.get("shelf_life", "")
    )
    row.unit, row.raw_unit, row.unit_inferred = unit, raw, inferred
    if problem:
        row.status = problem
        return row
    row.quantity = qty
    row.per_unit = round((low + high) / 2 / qty, 6)

    rest = clean[m.end():]
    if _PAY_APP.search(rest) and _PRICE.search(rest):
        # "27₪ (33₪ تطبيق)": the first price is cash, the app one follows it.
        row.payment = "cash"
    elif _PAY_APP.search(clean):
        row.payment = "app"
    elif _PAY_CASH.search(clean):
        row.payment = "cash"
    row.status = "ok"
    return row


# --- the export ------------------------------------------------------------

_REGIONS = [
    ("north_gaza", re.compile(r"شمال")),
    ("gaza_city", re.compile(r"مدينه غزه|غزه المدينه")),
    ("khan_younis", re.compile(r"خانيونس|خان يونس")),
    ("deir_al_balah", re.compile(r"دير البلح")),
    ("nuseirat", re.compile(r"النصيرات")),
    ("rafah", re.compile(r"رفح")),
    ("middle_area", re.compile(r"الوسطي")),
    ("south_gaza", re.compile(r"جنوب")),
]


def region_of(header: str) -> str:
    h = normalise(header)
    for name, pattern in _REGIONS:
        if pattern.search(h):
            return name
    return "gaza"


_MESSAGE = re.compile(r'<div class="message default[^"]*" id="message(\d+)">')
_DATE = re.compile(r'class="pull_right date details" title="(\d\d)\.(\d\d)\.(\d{4})')
_TEXT = re.compile(r'<div class="text">(.*?)</div>', re.S)


def posts(export_html: str):
    """(message_id, posted_on, text) for every message with text, in order."""
    starts = [(m.start(), int(m.group(1))) for m in _MESSAGE.finditer(export_html)]
    for k, (pos, mid) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(export_html)
        block = export_html[pos:end]
        d, t = _DATE.search(block), _TEXT.search(block)
        if not (d and t):
            continue
        body = re.sub(r"<br\s*/?>", "\n", t.group(1))
        text = html.unescape(re.sub(r"<[^>]+>", "", body))
        yield mid, date(int(d.group(3)), int(d.group(2)), int(d.group(1))).isoformat(), text


def parse_export(export_html: str, items: dict[str, dict]) -> list[PriceLine]:
    index = _alias_index(items)
    rows: list[PriceLine] = []
    for mid, posted_on, text in posts(export_html):
        lines = [l for l in text.split("\n") if l.strip()]
        region = region_of(" ".join(lines[:3]))
        for line in lines:
            row = parse_line(
                line, items, message_id=mid, posted_on=posted_on, region=region, index=index
            )
            if row:
                rows.append(row)
    return rows


def catalogue_items() -> dict[str, dict]:
    """Every seeded item with its Arabic names, unit and role."""
    from app.kitchen.catalogue import CATALOGUE, RAW_DIR, canonical_slug

    items: dict[str, dict] = {}
    for slug, _en, name_ar, unit, _sl, role, *_ in CATALOGUE:
        items[slug] = {
            "name_ar": name_ar,
            "aliases_ar": [],
            "unit": unit.value,
            "role": role.value,
            "shelf_life": _sl.value,
        }
    for path in sorted(RAW_DIR.glob("*_items.csv")):
        with path.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                slug = canonical_slug(r["slug"])
                aliases = [a for a in r["aliases_ar"].split("|") if a]
                if slug in items:
                    # Researched aliases still help find a hand-written item.
                    items[slug]["aliases_ar"] += [r["name_ar"], *aliases]
                    continue
                items[slug] = {
                    "name_ar": r["name_ar"],
                    "aliases_ar": aliases,
                    "unit": r["unit"],
                    "role": r["role"],
                    "shelf_life": r["shelf_life"],
                }
    return items


def write_csv(rows: list[PriceLine], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    names = [f.name for f in fields(PriceLine)]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=names)
        w.writeheader()
        for row in rows:
            w.writerow(asdict(row))


def read_csv(path: Path) -> list[PriceLine]:
    out = []
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            out.append(
                PriceLine(
                    message_id=int(r["message_id"]),
                    posted_on=r["posted_on"],
                    region=r["region"],
                    line=r["line"],
                    status=r["status"],
                    item_slug=r["item_slug"],
                    price_low=float(r["price_low"]),
                    price_high=float(r["price_high"]),
                    quantity=float(r["quantity"]),
                    unit=r["unit"],
                    raw_unit=r["raw_unit"],
                    unit_inferred=r["unit_inferred"] == "True",
                    payment=r["payment"],
                    per_unit=float(r["per_unit"]),
                )
            )
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().split("\n\n")[-2])
        return 2
    rows = parse_export(Path(argv[1]).read_text(encoding="utf-8"), catalogue_items())
    write_csv(rows, Path(argv[2]))
    from collections import Counter

    for status, n in Counter(r.status for r in rows).most_common():
        print(f"{n:5}  {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

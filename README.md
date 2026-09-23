# Masroufi — مصروفي

Expense tracking for Gaza households, built around the one piece of data that is
actually available: a bank statement export and its note field.

The app imports a statement, works out what each transaction was for, and asks
the household about the ones it is unsure of. Everything else — price data,
forecasting, savings advice — is built on top of that and is deliberately not
required for it to be useful.

**Status:** Phase A. Import, categorise, review, and detect repeat patterns.

---

## Why it is shaped this way

**There is no Bank of Palestine API.** Palestine is not a regulated open-banking
market, and the aggregators that paper over that elsewhere (Plaid, Tink,
TrueLayer) have no coverage there. So the statement export is not a fallback path
— it is *the* path, and the importer is the most important piece of the app.

**The note field is the only reliable identifier.** It is also often useless: a
bare `POS 3352119`. So the app does three things in order of cost:

1. **Exact grouping, free.** Repeat vendors and repeat amounts are found by
   arithmetic. A ₪3 charge appearing twelve times is a pattern whether or not
   anything understands what it is.
2. **A classifier, cheap.** The note goes to a model that returns one of a fixed
   set of categories with a calibrated confidence.
3. **The household, expensive.** Only the low-confidence cases are shown to the
   user. Their answer is final and is never overwritten by the model.

**No scraped social media data.** The plan to scrape grocery prices off Facebook
and Instagram was dropped: it breaks Meta's terms, Meta actively blocks it, and
it would make the most fragile component load-bearing. Phase B uses
[WFP Food Prices for State of Palestine](https://data.humdata.org/dataset/wfp-food-prices-for-state-of-palestine)
and [PCBS Prices of Basic Commodities in Gaza](https://data.humdata.org/dataset/state-of-palestine-price-of-basic-commodities-in-gaza)
from HDX, plus price reports submitted by users in the app — which is fresher and
more local than any scrape, and legal.

## Where Jev fits

[Jev](https://typesafe.ai/) is a TypeSafe "System One" model: you give it a state
and a set of typed questions, and it returns a decision per question with
calibrated probabilities rather than generated text. It cannot return a category
that is not in the taxonomy.

That maps onto this app almost exactly. One request per unique note, three
questions, evaluated in parallel ([app/classify/jev.py](app/classify/jev.py)):

| Question | Type | Used for |
|---|---|---|
| `category` | `Choice` over [the taxonomy](app/taxonomy.py) | which expense bucket |
| `is_merchant` | `Noul` | whether the note names a real vendor or is bank filler |
| `reducibility` | `Score` | feeds the savings advice in Phase C |

The `confidence` on the `Choice` is what drives the review queue. Below
`CONFIDENCE_THRESHOLD` the transaction is queued for the household instead of
being auto-categorised, so the app asks about the twenty transactions it is
unsure of rather than all three hundred.

**Cost is the real reason for Jev here.** A year of statements is thousands of
transactions; one LLM call each is not affordable for this app's users. Decisions
are also cached by normalised note, so the same grocer is classified once no
matter how often it appears.

**What Jev does not do:** find the patterns (arithmetic is exact and free), parse
the file, or write the savings advice. It labels; the code decides.

### The tidying layer, and the vendor dataset

A statement note is not a vendor name. It is a vendor name wrapped in whatever
the payment network added:

```
POS بطاقة سوبر ماركت الأمل 884213
  redact      POS بطاقة سوبر ماركت الأمل [NUM]
  translate   POS Al Amal Supermarket           (only if non-Latin)
  tidy        Al Amal Supermarket               <- stored
  match key   al amal supermarket               <- vendors matched on this
```

Tidying ([app/tidy.py](app/tidy.py)) is pure string work — exact, free, no model.
It strips payment-network filler in English and Arabic, drops bare reference
numbers, and produces a match key with sorted tokens so `AL AMAL SUPERMARKET`
and `SUPERMARKET AL AMAL` resolve to one vendor.

What survives goes into the **vendor table**, which is the dataset this layer
exists to build. On the sample statement, 31 raw notes become 10 clean vendors
with spend totals and date ranges.

The dataset then makes the app cheaper and better the longer it is used:

- The model proposes a **provisional** category for a vendor; a person
  **confirms** it. Confirmed is never overwritten by a model.
- `POST /api/vendors/{id}/confirm` settles one vendor's whole history and every
  future transaction from it, **with no model call at all**. One answer about the
  grocer covers five transactions now and all of next month's.
- Correcting a single transaction teaches its vendor by default, backfilling
  that vendor's unreviewed transactions — but never ones the household already
  answered itself.

Vendor matching is exact-alias first, then a conservative fuzzy merge above
`VENDOR_MATCH_THRESHOLD` (0.92). High on purpose: a missed match leaves two rows
the user can merge, while a wrong match quietly corrupts their history and their
price data. Every spelling seen is kept in `vendoralias`, so a bad merge can be
traced back to the note that caused it.

### Arabic notes, and the translation layer

BOP notes are Arabic, transliterated Arabic, or a mix, and whether Jev handles
that well is **not yet measured**. Two defences are in place:

1. The classifier sits behind a one-method interface
   ([app/classify/base.py](app/classify/base.py)), so replacing it is a
   single-file change.
2. An optional **translation step** normalises non-Latin notes to English before
   they reach the decision model ([app/classify/translating.py](app/classify/translating.py)).

The pipeline is **clean (redact → translate → tidy) → known vendor? → decide**,
deduplicated at every step, so a repeat vendor costs neither a translation nor a
classification after the first sighting, and a *confirmed* vendor costs nothing
ever again. Notes already in Latin script skip the translation call entirely.

Translation is **off by default**, on purpose. It adds a second provider to the
path and a call per unique note, so it should be switched on because Jev was
measured to struggle with Arabic, not pre-emptively. Turn it on with
`TRANSLATE_NOTES=true` and an `OPENROUTER_API_KEY`; the model is any OpenRouter
id, set with `OPENROUTER_MODEL`.

A failed translation falls back to the original note rather than failing the
import — the classifier then does what it can, and a low confidence sends the
transaction to review, which is the correct outcome.

## Privacy

This is the financial data of people under blockade, so:

- Notes are **redacted before leaving the machine** — IBANs, card numbers,
  phone numbers, emails and long reference numbers are stripped, and only the
  merchant text and the amount are sent ([app/redact.py](app/redact.py)). This
  holds for the translation provider too, not just the classifier;
  `test_jev_sends_redacted_state` and `test_note_is_redacted_before_translation`
  pin both.
- Raw uploaded files are never persisted; only parsed rows are stored.
- `data/private/` and `*.db` are gitignored. **Never commit a real statement.**
  The sample in `data/` is synthetic.
- The default backend is `mock`, which makes no network calls at all.

## Running it

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env

uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000/docs> and import `data/sample_statement.csv` from the
`POST /api/import` form.

`CLASSIFIER_BACKEND=mock` is the default and needs no API key or network — it
uses keyword rules and returns low confidence on anything it does not recognise.
Set `CLASSIFIER_BACKEND=jev` and `TYPESAFE_API_KEY` to use the real model.

```bash
pytest
```

## API

| | |
|---|---|
| `GET /api/sources` | The providers you can import from: `bop`, `jawwalpay`, `palpay`, `cash`, `manual`. |
| `POST /api/import?source=bop` | Upload an export for one provider. Deduplicates against what that source already holds. |
| `GET /api/transfers/pending` | Suspected movements between your own accounts that need an answer. |
| `POST /api/transfers/link` | Confirm a pair is one movement, excluding it from spending. |
| `GET /api/transactions?needs_review=true` | The review queue — the ones to actually ask about. |
| `PATCH /api/transactions/{id}/category` | The household's correction. Marked `user`; never overwritten. Teaches the vendor by default. |
| `GET /api/vendors` | The vendor dataset: tidied names, spend totals, provisional or confirmed categories. |
| `POST /api/vendors/{id}/confirm` | Confirm a vendor once; settles its whole history and all future transactions, with no model call. |
| `GET /api/patterns` | Detected repeat patterns with a monthly cost estimate and a question to put to the user. |
| `POST /api/patterns/confirm` | Name a pattern once; applies to every transaction in it. Eight ₪3 fares are one question, not eight. |
| `GET /api/summary` | Spend by category, plus what fraction is categorised. |
| `GET /api/categories` | The taxonomy, for building a picker. |

### Kitchen

| | |
|---|---|
| `GET`/`PATCH /api/kitchen/household` | Adults, children, and the child portion ratio. |
| `GET /api/kitchen/dishes` · `/items` | The seeded dish library and grocery catalogue. |
| `GET`/`PUT /api/kitchen/dishes/{id}/ingredients` | Read and correct a recipe's per-adult quantities. |
| `PATCH /api/kitchen/items/{id}` | Correct an item's unit, shelf life or purchase step. |
| `PUT /api/kitchen/dishes/{id}/portion` | Remember that this household needs more or less of a dish. |
| `POST`/`GET`/`DELETE /api/kitchen/plan` | Plan a meal into a day and slot; read or clear the week. |
| `POST /api/kitchen/shopping-list` | Build the week's lists. Preserves anything already bought. |
| `GET /api/kitchen/shopping-list` | The two lists: `daily` keyed by day, `weekly` aggregated. |
| `POST /api/kitchen/shopping-list/{id}/purchase` | Confirm a purchase → a transaction and a price per kg. |
| `GET /api/kitchen/shopping-list/{id}/substitutes` | Alternatives from the same role. |
| `POST /api/kitchen/shopping-list/{id}/unavailable` | Record a gap, optionally swapping in a substitute. |
| `GET /api/kitchen/storage-advice` | Effective shelf life at today's temperature, and advice only where it matters. |
| `GET /api/kitchen/dishes/gas` | Dishes ranked by cooking gas; `max_kg` filters to what a short cylinder allows. |
| `POST /api/kitchen/gas-budget` | Record the gas available for a period, and its price. |
| `GET /api/kitchen/plan/gas` | What the week's plan burns, against the budget, with a way to cut it. |
| `POST /api/kitchen/shopping-list/{id}/from-stock` | "We already have this." Clears the line and deducts from the cupboard. |

### Pantry

| | |
|---|---|
| `POST /api/pantry/parcel` | Record an aid delivery as one bundle. |
| `POST`/`GET /api/pantry/stock` | Add a holding; read the cupboard with how much shelf life storage has used up. |
| `PATCH /api/pantry/stock/{id}/condition` | Only the household marks something spoiled. |
| `PUT /api/pantry/items/{id}/feeling` | Mark something they have had enough of. No reason asked. |
| `GET /api/pantry/suggestions` | What to cook, weighing the cupboard against the fatigue. |

### Prices

| | |
|---|---|
| `POST /api/prices/observations` | Record a price. Corrections are observations too, never overrides. |
| `GET /api/prices/observations/{id}` | The history behind an estimate, so a figure can be audited. |
| `GET /api/prices/estimate/{id}` | Current best guess, with range, staleness and certainty. 404 when unknown. |
| `GET /api/prices/estimates?stale_only=true` | What needs re-checking. |
| `GET /api/prices/shopping-list/cost` | What a week is likely to cost, with its gaps named. |

### Offline

| | |
|---|---|
| `GET /api/bootstrap` | One versioned payload the client stores and then works from with no connection. |

Every write that happens in a shop — purchase, unavailable, from-stock, parcel,
stock — takes a client-generated `key` so a replayed queue cannot record it twice.

## Importer

Written against no particular bank. Column names are matched against aliases in
English and Arabic, both `Amount` and `Debit`/`Credit` layouts are handled, and
`utf-8-sig` / `cp1256` encodings are tried in turn.

**To add BOP:** get one real export, redact it, add its headers to the alias
lists in [app/importers/csv_import.py](app/importers/csv_import.py). That should
be the whole change. If BOP exports PDF rather than CSV, a PDF table extractor
goes in front of the same parser.

## Roadmap

- **A — done.** Multi-source import, note cleaning, categorisation, vendor
  dataset, recurring patterns, deduplication, internal transfers.
- **A2 — done.** Meal planning, the two shopping lists, purchase confirmation.
- **B — next.** Screenshot and receipt capture. This is the real unlock: JawwalPay
  and PalPay may have no export at all, so a screenshot is the only way in, and a
  receipt's line items are the price dataset. Approved shape: capture always
  queues locally and parses when a connection appears; extraction covers total
  *and* line items; a receipt auto-matches the statement line that arrives later
  and asks only when unsure.
- **C.** HDX price ingest to compare against; forecast next month from the
  confirmed patterns; savings suggestions ranked by `reducibility` and by the gap
  between what the household paid and the going rate.

## Meal planning and the two shopping lists

A week of planned meals becomes a shopping list with quantities scaled to the
household. The point is not convenience — it is that buying the right amount
costs less and, with refrigeration scarce, does not go bad.

**The split is by shelf life, not by meal.** Aggregating the whole week means
perishables spoil; splitting everything daily means seven trips and no bulk
pricing. So each item carries a shelf life judged *without* refrigeration, and one
plan produces two lists:

```
=== BUY TODAY (perishable) ===
  2026-07-06    500 g   خبز        for Ful medames
               1000 g   دجاج       for Maqluba with chicken
  2026-07-08    500 g   خبز        for Ful medames
               1000 g   دجاج       for Molokhia

=== BUY ONCE THIS WEEK (keeps) ===
   2500 g  أرز        1000 g  عدس        1500 g  بصل
    500 ml زيت نباتي   2250 g  فول         300 g  معجون بندورة
```

Chicken appears only on the two days it is cooked. Rice, lentils and oil appear
once. Eggs and root vegetables keep a few days without a fridge, so they go on the
weekly list; bread and leafy greens do not, so they go on the daily one.

**Aggregating happens before rounding.** Seven days of 80 g of rice is 750 g for
two adults. Rounding each meal up to a 250 g purchase step first would have
bought 1.75 kg — the rounding waste multiplied by every meal in the week.

### Working offline

First launch may assume a connection. Nothing after it may.

`GET /api/bootstrap` returns one versioned payload the client stores: items,
dishes with their gas cost, the household, sources, categories, current stock,
weary items, price estimates, the week's plan and lines, the week's temperatures,
and the twelve monthly normals.

**The server ships the outputs of its models, not the models.** Porting the Q10
curve, the portion maths and the price-decay weighting to TypeScript would mean two
implementations of the rules this app's correctness claims rest on, and they would
drift. So each item arrives with `keeps_days_by_day` already evaluated at each of
the week's temperatures, and the client's rule is one subtraction and one
comparison — *does it keep longer than the days I would hold it* — with no physics
on the device. The raw inputs (`keeps_days_at_20c`, `q10`) and the monthly normals
come too, so a client can still split a list for a week it has no forecast for.

**Writes are replayable.** Every write that happens in a shop happens with no
signal, so purchase, unavailable, from-stock, parcel and stock all accept a
client-generated `key`. The first call does the work and its response is stored; a
repeat of the same key returns that response and changes nothing.

The key is generated **when the person taps, not when the request is sent** — a key
made at send time would differ between the original and the retry, which defeats
the point. A replayed purchase that is not recognised as a replay records the spend
twice, and an app that lies about what a household spent is worse than no app; there
is a test pinning exactly that.

### No price is a fact

WFP has recorded food prices in Gaza moving by tens of percent inside a month. A
stored price is therefore worse than none: it is wrong silently, and somebody
plans around it.

So **there is no price column anywhere**. There is a log of dated observations,
each with a source and a shop, and the current estimate is computed on demand:

```
rice observed:  4.00/kg (120d ago, reference) · 7.50 (40d) · 11.00 (6d) · 12.50 (1d)

estimate        11.51/kg        <- a flat mean would say 8.75
range           11.00 – 12.50 over the last month
spread          13%
certainty       fair · newest 1 day old · from manual, purchase, reference
```

Four rules make that number trustworthy:

- **Recent observations dominate.** Weight halves every `PRICE_HALF_LIFE_DAYS`
  (14 by default — prices here move), so last week's price is not averaged flat
  into one from three months ago.
- **First-hand beats reference.** What this household paid, or read off a shelf,
  outweighs a regional average published for a different market.
- **The spread is reported, not hidden.** When recent observations disagree, a
  range is the honest answer and a single number is not. `certainty` is a coarse
  `good` / `fair` / `poor` by documented rules, rather than a fabricated 0.87
  nobody can interpret.
- **No observations means no estimate.** `GET /api/prices/estimate/{id}` returns
  404, not a guess — *"Add an observation rather than relying on a guess."* An
  invented price here loses someone money.

Prices arrive four ways, and all four are the same kind of record:

| Source | Weight | Where it comes from |
|---|---|---|
| `purchase` | 1.0 | Confirming a planned purchase logs one automatically |
| `manual` | 1.0 | The household typed it, or read it off a shelf |
| `receipt` | 1.0 | Receipt OCR, once that exists |
| `crowd` | 0.7 | Another household's shop, later |
| `reference` | 0.6 | WFP / PCBS market averages, later |

**A manual correction is not an override that sticks.** It is a fresh, heavily
weighted observation — which is what keeps the estimate current instead of
accumulating stale "fixes" that outlive their truth. Nothing is ever overwritten,
and `GET /api/prices/observations/{id}` shows the history behind any figure.

Costing a whole list names its own gaps rather than quietly covering half of it:

```
Rice            250g  ~ 2.88  (fair)
Lentils         250g      ?   (no price recorded)
Onions          250g      ?   (no price recorded)

known cost 2.88 ILS
! Covers 1 of 6 lines. 5 have no recorded price.
```

The same estimates reach the meal suggestions, which turns *"a different meal for
the price of a spice"* into an actual figure — and `missing_cost_partial` stops a
total being quoted when some ingredient has no price, because a figure that
silently omits items is worse than no figure.

### The cupboard comes first

The planner was built assuming you buy everything you cook. For a household
holding months of aid staples that is simply wrong, and it produced a shopping
list that asked for **more lentils than anyone could eat**.

So the list subtracts the cupboard before it asks for money. A household given
8 kg of lentils and 12 kg of rice, planning mujaddara all week, is asked to buy
onions and cumin — nothing else.

```
=== THE CUPBOARD at 31°C ===
  Wheat flour     15000g   held 155d of  141d  ####################
  Lentils          8000g   held 155d of  322d  #########
  Rice            12000g   held 155d of  322d  #########

! Wheat flour has been stored 155 days. At around 31°C that is past the
  141 days it normally keeps — worth checking before you plan a meal around it.
```

**Aid is a first-class source, not a kind of purchase.** It arrives as a bundle,
so `POST /api/pantry/parcel` records a whole delivery in one call — asking a
household to enter eight items separately means nobody enters any of them.

**Stock degrades, and the shelf-life model already knew how.** The same Q10
curve that decides the daily/weekly split computes how much of a holding's life
storage has used up. That flour warning is not a guess; it is 155 days measured
against the 141 that flour keeps at 31 °C.

Two rules the pantry follows so it can be trusted:

- **Only the household can call something spoiled.** The app flags risk from time
  and temperature and stops there. Stock marked spoiled is excluded from cover
  entirely, because wrongly counting it suppresses a purchase someone actually
  needs — the one failure here that costs a person a meal.
- **Stock is spent worst-first.** At-risk before sound, then oldest before
  newest, so the cupboard drains in the order that wastes least.

`POST /api/kitchen/shopping-list/{id}/from-stock` is the one-tap "we already have
this", and it deducts from recorded stock so next week's list knows too. A
household that has not recorded its cupboard can pass `deduct: false` and just
clear the line.

### Eating the same thing for months

A cupboard full of pulses plus a planner that optimises for cost produces one
recommendation: lentils. Which is exactly what people have been eating for
months, and a plan nobody will follow is worth nothing.

`GET /api/pantry/suggestions` ranks dishes on what is in the cupboard, what has
been eaten lately, and what the household has said it is tired of. Three
decisions in it matter more than the scoring:

**Weariness is absolute, not a term to trade off.** Marking lentils `weary` puts
every dish the household is *not* tired of above every dish it is — however well
stocked or cheap the lentils are. They are still listed, never hidden, and
`PUT /api/pantry/items/{id}/feeling` asks for no reason and stores none. No
explanation the app produces contains the words cheap, healthy or should; there
is a test asserting that.

**Repetition is judged on the base, not the aromatics.** Nobody gets tired of
onions. What people report being unable to face again is lentils and bread, so
only grains, pulses, bread, meat, fish, eggs and dairy count towards repetition —
onion, garlic, oil and spice are background however often they appear. A test
caught this: shakshuka was being scored as a repeat of mujaddara because they
share an onion.

**The cheapest variety is a spice, not a protein.** You cannot conjure a new
protein out of a cupboard of pulses. But a dish whose only missing ingredient is
a paste or a spice is a genuinely different meal for a few shekels, and the app
can say exactly which few — that is the `flavour_only` flag, and it is the most
useful thing here.

```
0.36  White bean stew              cover 59%   3 things to buy
0.09  Maqluba with chicken         cover 43%   3 things to buy
      ─── below everything else, still visible ───
0.20  Mujaddara                    Uses Lentils, which you have had enough of.
```

That bonus is **withheld when the base is the weary one**: swapping the spice does
not make it a different meal in the way that matters. The first version of this
ranked weary lentil dishes top because the bonus outweighed the penalty — which
was the app nudging back, and is now impossible by construction.

What this does not do, and does not pretend to: make a household that only has
lentils have something other than lentils.

### Heat decides what "perishable" means

A static shelf-life label is wrong half the year. Bread bought on a 17 °C January
day can reasonably last until tomorrow; the same bread on a 31 °C August day
cannot. So shelf life is computed, not declared, using the **Q10 rule** — the
standard simplification of the Arrhenius relationship used in shelf-life work:

```
days(T) = days_at_20C × Q10 ** ((20 − T) / 10)
```

Q10 is the factor by which spoilage speeds up per 10 °C — about 2 for dry goods,
2.5 for bread and fresh produce, 3.5–4 for meat and fish where microbial growth
drives it. It is stored per item ([app/kitchen/catalogue.py](app/kitchen/catalogue.py))
rather than as one global constant, because a sack of rice and a chicken do not
respond to heat the same way.

**The comparison is against how long the item would be held, not a fixed
threshold.** This was wrong in the first version and a reviewer caught it: the code
asked *"does this keep at least two days"* when the question is *"will this survive
from the weekly shop until the day it is cooked"*. Bread keeping four days is fine
for Tuesday's meal and useless for Saturday's, so a week of bread was being put in
one weekly line and the Saturday loaf bought five days early.

Now an item goes on the weekly line only as far as it can actually be held, and the
line says how far that is:

```
bread, every day, 17.5 °C   weekly 1000 g, covers through Thursday
                            + fresh on Friday, Saturday, Sunday

bread, every day, 31.0 °C   fresh every day, seven lines

chicken, every day, 12 °C   fresh every day — under the two-day floor
                            at any temperature
```

Shelf life is taken at the **hottest day it would be stored through**, not the day
it is eaten: bread bought on a cool Monday for a cool Friday still has to survive a
hot Wednesday in between. And anything under the two-day floor is always same-day,
which is what stops an item needed on the shop day itself appearing on both lists.

**Temperature comes from [Open-Meteo](https://open-meteo.com/), which needs no API
key** — one less credential to manage. It is off by default (`WEATHER_ENABLED`);
with it off, each item's static label decides, which is exactly the behaviour from
before this existed.

Two deliberate limits:

- **A weather outage never fails a shopping list.** Any day the service cannot
  supply falls back to the Gaza [climate normals](app/weather.py) for that month,
  flagged `estimated` so a caller can say it is a guess rather than a forecast.
  The forecast endpoint only reaches a couple of weeks back, so planning a week
  in the distant past falls back too.
- **The model floor is 10 °C, not 0 °C.** Extrapolating the curve to freezing says
  raw chicken keeps five days — true in a fridge, and precisely the assumption this
  app does not make. Clamping there also means a real cold snap is treated as a
  10 °C day, which understates shelf life slightly: an error towards buying fresher.

`GET /api/kitchen/storage-advice` returns the effective shelf life of each item at
today's temperature, plus a note **only where the heat changes what someone should
do**. An empty list of notes is a valid, useful answer.

### Cooking gas is part of a recipe's price

Where fuel is scarce, what a dish costs to cook belongs in the budget. A pot of
beans simmered for an hour and a quarter can cost more in gas than the beans cost
in the shop, and nothing in a normal budgeting app would ever show that.

```
kg = burners × (full_flame_hours × 0.25 + simmer_hours × 0.10)
```

A single domestic LPG burner runs at roughly **0.25 kg/h** at full flame, and a low
simmer well under half that. As a sanity check, a family of five or six cooking
three meals a day is reported to use around 0.3 kg/day, which is the order of
magnitude this produces — there is a test pinning that.

The seeded library, cheapest to cook first:

```
0.000 kg    Bread with zaatar and oil     <- no cooking at all
0.042 kg    Eggs with tomato
0.108 kg    Mujaddara                     (soaking saves 0.023 kg)
0.167 kg    White bean stew               (soaking saves 0.050 kg)
0.317 kg    Maqluba with chicken          <- 65 min across two burners
```

A household records what gas it has (`POST /api/kitchen/gas-budget`) — nothing
else can know it, since a cylinder's remaining weight is not on any statement —
and `GET /api/kitchen/plan/gas` checks the week's plan against it:

```
This plan needs 1.212 kg of gas but only 0.5 kg is available — short by
0.712 kg. Soaking the pulses in White bean stew overnight would save
about 0.05 kg.
```

`GET /api/kitchen/dishes/gas?max_kg=0.1` is the other half: when the cylinder is
short, this is how a household finds what it can still afford to cook.

These rates are averages over unknown stoves, pots and lid discipline — honest to
about a third, not to the gram — so `BURNER_KG_PER_HOUR` and `SIMMER_KG_PER_HOUR`
are settings a household that knows its own cylinder can calibrate.

**Quantities scale by adult equivalents.** The household is two numbers, adults
and children, with a child counting as 0.6 of an adult portion by default and a
per-dish override the app remembers for when that is wrong.

A consequence worth knowing: for a small household over a short week, the
**purchase step decides the quantity more than the portion maths does** — you
cannot buy 120 g of lentils. The listed amount is what a shop can actually sell,
which is the honest number to show, but it means a modest portion override may
not change the list at all.

**Confirming a purchase writes a transaction** — category `groceries`, marked as
the household's own answer — and records a price per kg. That means **the planner
seeds the price dataset before receipt reading exists**: a quantity, a price, a
date and a shop is an item-level price observation, in the same units the
[WFP](https://data.humdata.org/dataset/wfp-food-prices-for-state-of-palestine) and
PCBS datasets use.

**When something is not at the shop**, one call marks it unavailable and offers
substitutes from the same role — a pulse for a pulse, never meat for a pulse, and
never something perishable in place of something that keeps. Recording the gap is
worth it even without a substitute: over time it is real data about what is scarce
and when.

> ### The seeded quantities need a local check
>
> The catalogue in [app/kitchen/catalogue.py](app/kitchen/catalogue.py) and the
> dishes in [app/kitchen/dishes.py](app/kitchen/dishes.py) were written by a
> developer, not by someone who cooks these dishes or shops in Gaza. Every
> quantity, shelf life and purchase step is a starting point.
>
> That now includes the Q10 values and the cooking times, which drive the
> shelf-life and gas maths respectively.
>
> They are seeded as **editable rows** for exactly that reason:
> `PUT /api/kitchen/dishes/{id}/ingredients` corrects a recipe and
> `PATCH /api/kitchen/items/{id}` corrects an item. Shelf life is the
> consequential one — changing it moves an item between the two lists.
> An over-stated quantity here is money wasted, which is the opposite of the point.

## Several providers, one picture

Households here pay through a bank *and* two or three wallets — Bank of
Palestine, JawwalPay, PalPay — and no single export shows the whole picture. So
every transaction belongs to a **source** ([app/sources.py](app/sources.py)), and
two problems follow from that.

**Re-imports must not duplicate.** Exports overlap, and people re-download them.
Deduplication is per source, and it **counts rather than matching**
([app/services/dedup.py](app/services/dedup.py)): treating `(date, amount, note)`
as unique would silently delete one of two real ₪3 fares taken on the same day
with the same useless note. If the file has three identical rows and the database
holds two, one is new. A provider reference is used instead whenever the export
supplies one, since that is exact.

**Money moved between your own accounts is not spending.** A JawwalPay wallet
topped up from BOP appears twice: `-200` in the bank export and `+200` in the
wallet export. Counting both inflates the household's spending by exactly what
they moved between their own pockets.

Matching is equal-and-opposite amounts, across different sources, within three
days — and that alone is deliberately *not* enough to act on, since two unrelated
₪200 payments a day apart look identical. A pair is auto-linked only when one
side's note names the other provider; everything else goes to
`GET /api/transfers/pending` for the household to answer. Same rule as duplicate
receipts: match confidently, ask when unsure.

```
import bop      TRANSFER TO JAWWALPAY   -200
import jawwalpay  TOP UP                +200   -> linked, excluded from spending
                  سوبر ماركت الأمل      -145   -> the only real expense
```

### Known gaps

- **Weather is off by default.** Turn it on with `WEATHER_ENABLED=true`. There
  is no per-household location setting yet — latitude and longitude are
  process-wide config.
- **Dietary needs per household member are not modelled.** No allergy,
  intolerance or medical-diet handling, and the portion maths treats every
  adult alike. `ItemPreference` is about fatigue, not health.
- **Nothing models cooking on anything but gas.** When the cylinder runs out
  and a household cooks on wood, a borrowed electric ring, or burns spoiled
  flour, the cost moves to a category the app does not track and the gas
  estimate is simply wrong.
- **AI generation for unknown dishes is not built.** The approved design is a
  seeded library plus generation for anything not in it, saved for reuse and
  flagged `needs_review`. The `Dish.source` and `needs_review` columns exist for
  it; nothing writes `source="ai"` yet. Until then, a dish outside the seeded
  twelve has to be added by hand.

- **Only CSV import exists.** JawwalPay and PalPay may have no export at all,
  only in-app history — which makes screenshot capture the primary ingestion path
  for them, not a fallback. Not built yet.
- **Receipts are not implemented.** Approved design: capture always queues
  locally and parses when a connection appears, extraction covers total *and*
  line items, and a receipt auto-matches against the statement line that arrives
  later, asking only when unsure. The line items are what gives Phase B a price
  dataset of its own.
- Small change under ₪20 is still handled in cash and leaves no digital trace.
  The `cash` and `manual` sources exist for it; nothing writes to them yet.
- Multi-currency amounts are stored but not normalised for reporting.
- The note and decision caches are per-process; the vendor table is the durable
  half.
- **No reference prices are loaded.** `PriceSource.REFERENCE` and `CROWD` are
  designed for and weighted, but nothing seeds them, so on a fresh install
  nearly every line reads "no price" until the household has bought things.
  A dated WFP/PCBS snapshot shipped in the bootstrap bundle would fix that;
  it is not built.
- **Receipt reading is not built.** `POST /api/receipts` → proposed line matches
  with confidence is the intended shape, and item matching is the piece it needs.
- **No expenses UI exists, and the tab bar in the mockups was wrong.** The gas
  screen was filed under expenses because a fourth tab was needed, not because
  it belongs there. The import / review / vendor / summary half of the app has
  no screens at all.
- **No auth.** Do not deploy as-is.

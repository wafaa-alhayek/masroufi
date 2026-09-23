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
- **No auth.** Do not deploy as-is.

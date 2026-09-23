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
| `POST /api/import` | Upload a statement export. Returns counts and how many classifier calls the cache saved. |
| `GET /api/transactions?needs_review=true` | The review queue — the ones to actually ask about. |
| `PATCH /api/transactions/{id}/category` | The household's correction. Marked `user`; never overwritten. Teaches the vendor by default. |
| `GET /api/vendors` | The vendor dataset: tidied names, spend totals, provisional or confirmed categories. |
| `POST /api/vendors/{id}/confirm` | Confirm a vendor once; settles its whole history and all future transactions, with no model call. |
| `GET /api/patterns` | Detected repeat patterns with a monthly cost estimate and a question to put to the user. |
| `POST /api/patterns/confirm` | Name a pattern once; applies to every transaction in it. Eight ₪3 fares are one question, not eight. |
| `GET /api/summary` | Spend by category, plus what fraction is categorised. |
| `GET /api/categories` | The taxonomy, for building a picker. |

## Importer

Written against no particular bank. Column names are matched against aliases in
English and Arabic, both `Amount` and `Debit`/`Credit` layouts are handled, and
`utf-8-sig` / `cp1256` encodings are tried in turn.

**To add BOP:** get one real export, redact it, add its headers to the alias
lists in [app/importers/csv_import.py](app/importers/csv_import.py). That should
be the whole change. If BOP exports PDF rather than CSV, a PDF table extractor
goes in front of the same parser.

## Roadmap

- **A — now.** Import, classify, review, patterns.
- **B.** HDX price ingest; in-app crowdsourced price reports; basket comparison.
- **C.** Forecast next month from the confirmed patterns; savings suggestions
  ranked by `reducibility` and by the gap between what the household paid and the
  going rate.

### Known gaps

- **Re-importing a statement duplicates its transactions.** There is no import
  deduplication yet. This is the same problem as reconciling a photographed
  receipt against the statement line that arrives a week later, and both want one
  answer rather than two.
- **Cash is invisible.** A ₪400 withdrawal shows as `cash_withdrawal`; what it
  was actually spent on leaves no bank trace at all. Receipts and manual entry
  are the only way to see inside it, and whether they *draw down* the withdrawal
  or are counted separately is an open product decision.
- Multi-currency amounts are stored but not normalised for reporting.
- The note and decision caches are per-process; the vendor table is the durable
  half.
- **No auth.** Do not deploy as-is.

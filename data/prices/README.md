# Scraped price observations

`asarsook_prices.csv` comes from the public web preview of the Telegram channel
**أسعار السوق - غزة** ([t.me/s/asarsook](https://t.me/s/asarsook)): 2,566 posts
from 2024-11-19 to 2026-10-20, of which 567 quote prices in shekels.

**Not reviewed, and not loaded into the app.** It is raw material for
`PriceObservation(source=REFERENCE)` rows, one day.

## How far to trust it

These are Gaza prices, but they come from one channel's posts, not from
receipts. Prices moved by an order of magnitude over this period: tomatoes were
about 90 ₪/kg in August 2025 and about 11 ₪/kg in January 2026. Never average
across dates.

Every row keeps the original line and a link to its post. `flags`:

| flag | meaning |
|---|---|
| `unit_assumed_kg` | no unit was given; produce, meat and fish are quoted per kilo by convention there |
| `unit_unknown` | no unit was given, and it is a packaged item, so the price can't be used without one |
| `question_not_quote` | the line asks a question ("who sold sugar at 500?"), not a quote |
| `two_prices_in_line` | old and new price in one line; the parser took the first |

A row with no flags gave an explicit unit. Item matching runs on Arabic names
and aliases from the catalogue, plus a short list of Gaza colloquial names in
the script, so brand-level matches (cheese especially) need a look.

## Regenerating

```bash
node scripts/scrape_asarsook.cjs                     # writes posts.jsonl, ~4 min, 1.5 s between pages
node scripts/extract_asarsook_prices.cjs .           # writes asarsook_prices.csv
```

Photo posts (726 of them) are price-list images and are not read; that needs OCR.

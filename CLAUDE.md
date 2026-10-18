# Masroufi

Expense tracking and kitchen planning for Gaza households. FastAPI + SQLModel.
Tests: `pip install -r requirements.txt && pytest`. The README explains every
design decision; read the relevant section before changing behaviour.

## Three repositories, one vocabulary

| repo | owns |
|---|---|
| **masroufi** (this one) | item and dish slugs, units, shelf life, purchase steps, prices |
| [kitchen-vocabulary](https://github.com/wafaa-alhayek/kitchen-vocabulary) | the join between Masroufi slugs and Zaytoun foods, and the yield factors |
| [Zaytoun](https://github.com/wafaa-alhayek/Zaytoun) | as-eaten nutrient panels and portions |

- `app/kitchen/vocabulary.json` is a **vendored copy**. Never edit it here. Copy
  it from kitchen-vocabulary `main`; CI fails if the two differ by a byte.
- **Renaming or removing a slug is a three-repo change**, merged in this order:
  1. this repo: rename it, and record the old name in `RAW_SLUG_ALIASES`
     (items, `app/kitchen/catalogue.py`) or `RENAMED_DISHES` (dishes,
     `app/kitchen/dishes.py`) so existing databases are renamed, not duplicated;
  2. kitchen-vocabulary: update `vocabulary.json`;
  3. refresh the vendored copy here and in Zaytoun (`src/data/vocabulary.json`).
- Adding an item: hand-written staples go in `CATALOGUE`, researched rows in
  `data/raw/*_items.csv`. Then it needs a vocabulary entry.

## Working alongside other Claude sessions

More than one session works on these repos at once, so:

- Start every task from a fresh `origin/main`, on a new branch.
- Never push to a branch you did not create, and never commit straight to `main`.
- One change per pull request. Its description names anything it depends on and
  the order to merge in.
- Do not merge pull requests. The owner reviews and merges.
- If `main` moved under you and conflicts, keep what is on `main` unless it is
  plainly wrong, and say what you dropped.

## Never

- Commit a real bank statement or anyone's financial data. `data/` holds only
  synthetic or published data.
- Store a price as a fact. Prices are dated observations (see the README).

import asyncio
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.classify import Classifier
from app.config import settings
from app.db import get_session
from app.deps import get_classifier
from app.importers.csv_import import ImportError_, parse_csv
from app.models import CategorySource, Currency, Transaction
from app.services.recurring import find_patterns
from app.taxonomy import CATEGORIES, REDUCIBILITY_SCALE, is_valid_category

router = APIRouter()

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class ImportSummary(BaseModel):
    batch: str
    imported: int
    auto_categorised: int
    needs_review: int
    classifier_calls_saved: int = Field(
        description="Transactions served from the note cache instead of a fresh classifier call."
    )


class TransactionOut(BaseModel):
    id: int
    booked_on: date
    amount: float
    currency: Currency
    note: str
    category: str | None
    category_source: CategorySource
    confidence: float | None
    reducibility: int | None
    needs_review: bool


class CategoryUpdate(BaseModel):
    category: str


class PatternConfirm(BaseModel):
    """Identifies a pattern by its shape, since patterns are derived, not stored."""

    amount: float
    currency: str = Currency.ILS.value
    note_key: str = ""
    category: str


class PatternOut(BaseModel):
    note_key: str
    amount: float
    currency: str
    occurrences: int
    median_gap_days: float
    monthly_estimate: float
    by_amount_only: bool
    first_seen: date
    last_seen: date
    prompt: str


@router.get("/categories")
def list_categories() -> dict:
    return {"categories": CATEGORIES, "reducibility_scale": REDUCIBILITY_SCALE}


@router.post("/import", response_model=ImportSummary)
async def import_statement(
    file: UploadFile = File(...),
    currency: Currency = Query(Currency.ILS, description="Used when the file has no currency column."),
    session: Session = Depends(get_session),
    classifier: Classifier = Depends(get_classifier),
) -> ImportSummary:
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "The uploaded file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "The file is larger than 5 MB.")

    batch = uuid.uuid4().hex[:12]
    try:
        transactions = parse_csv(raw, batch=batch, default_currency=currency)
    except ImportError_ as exc:
        raise HTTPException(422, str(exc)) from exc

    before = _cache_size(classifier)
    results = await asyncio.gather(
        *(classifier.classify(t.note, t.amount, t.currency.value) for t in transactions),
        return_exceptions=True,
    )

    auto = 0
    for transaction, result in zip(transactions, results):
        if isinstance(result, BaseException):
            # A failed classification is not a failed import. The transaction is
            # stored uncategorised and can be retried or set by hand.
            transaction.needs_review = True
            continue
        transaction.category = result.category
        transaction.confidence = result.confidence
        transaction.reducibility = result.reducibility
        transaction.category_source = CategorySource.MODEL
        transaction.needs_review = result.needs_review(settings.confidence_threshold)
        if not transaction.needs_review:
            auto += 1

    session.add_all(transactions)
    session.commit()

    unique_after = _cache_size(classifier)
    fresh_calls = max(unique_after - before, 0)
    return ImportSummary(
        batch=batch,
        imported=len(transactions),
        auto_categorised=auto,
        needs_review=sum(1 for t in transactions if t.needs_review),
        classifier_calls_saved=max(len(transactions) - fresh_calls, 0),
    )


@router.get("/transactions", response_model=list[TransactionOut])
def list_transactions(
    needs_review: bool | None = None,
    category: str | None = None,
    batch: str | None = None,
    limit: int = Query(200, le=1000),
    session: Session = Depends(get_session),
) -> list[Transaction]:
    statement = select(Transaction)
    if needs_review is not None:
        statement = statement.where(Transaction.needs_review == needs_review)
    if category is not None:
        statement = statement.where(Transaction.category == category)
    if batch is not None:
        statement = statement.where(Transaction.import_batch == batch)
    statement = statement.order_by(Transaction.booked_on.desc()).limit(limit)
    return list(session.exec(statement))


@router.patch("/transactions/{transaction_id}/category", response_model=TransactionOut)
def set_category(
    transaction_id: int,
    update: CategoryUpdate,
    session: Session = Depends(get_session),
) -> Transaction:
    """The user's correction. Marked as USER so re-classification never undoes it."""
    if not is_valid_category(update.category):
        raise HTTPException(422, f"Unknown category: {update.category!r}")

    transaction = session.get(Transaction, transaction_id)
    if transaction is None:
        raise HTTPException(404, "No such transaction.")

    transaction.category = update.category
    transaction.category_source = CategorySource.USER
    transaction.confidence = 1.0
    transaction.needs_review = False
    session.add(transaction)
    session.commit()
    session.refresh(transaction)
    return transaction


@router.get("/patterns", response_model=list[PatternOut])
def list_patterns(
    min_occurrences: int = Query(3, ge=2, le=50),
    session: Session = Depends(get_session),
) -> list[PatternOut]:
    transactions = list(session.exec(select(Transaction)))
    out = []
    for p in find_patterns(transactions, min_occurrences=min_occurrences):
        if p.by_amount_only:
            prompt = (
                f"{p.currency} {abs(p.amount):.2f} appears {p.occurrences} times, "
                f"about every {p.median_gap_days:.0f} days, with no useful note. "
                "What is this?"
            )
        else:
            prompt = (
                f"'{p.note_key}' appears {p.occurrences} times at "
                f"{p.currency} {abs(p.amount):.2f}. Is this a regular cost?"
            )
        out.append(PatternOut(**{**vars(p), "monthly_estimate": p.monthly_estimate, "prompt": prompt}))
    return out


@router.post("/patterns/confirm")
def confirm_pattern(
    confirm: PatternConfirm,
    session: Session = Depends(get_session),
) -> dict:
    """Name a pattern once and apply it to every transaction in it.

    This is the point of detecting patterns at all: eight identical ₪3 fares are
    one question to the household, not eight.
    """
    if not is_valid_category(confirm.category):
        raise HTTPException(422, f"Unknown category: {confirm.category!r}")

    transactions = list(session.exec(select(Transaction)))
    match = next(
        (
            p
            for p in find_patterns(transactions)
            if round(p.amount, 2) == round(confirm.amount, 2)
            and p.currency == confirm.currency
            and p.note_key == confirm.note_key
        ),
        None,
    )
    if match is None:
        raise HTTPException(404, "No pattern matches that amount, currency and note.")

    by_id = {t.id: t for t in transactions}
    for tid in match.transaction_ids:
        transaction = by_id[tid]
        transaction.category = confirm.category
        transaction.category_source = CategorySource.USER
        transaction.confidence = 1.0
        transaction.needs_review = False
        session.add(transaction)
    session.commit()

    return {"category": confirm.category, "transactions_updated": len(match.transaction_ids)}


@router.get("/summary")
def summary(session: Session = Depends(get_session)) -> dict:
    """Spend by category, plus what is still unreviewed."""
    transactions = list(session.exec(select(Transaction)))
    by_category: dict[str, float] = {}
    for t in transactions:
        if t.amount < 0 and t.category:
            by_category[t.category] = by_category.get(t.category, 0.0) + abs(t.amount)

    return {
        "total_out": round(sum(abs(t.amount) for t in transactions if t.amount < 0), 2),
        "total_in": round(sum(t.amount for t in transactions if t.amount > 0), 2),
        "by_category": {k: round(v, 2) for k, v in sorted(by_category.items(), key=lambda kv: -kv[1])},
        "needs_review": sum(1 for t in transactions if t.needs_review),
        "coverage": round(
            sum(1 for t in transactions if not t.needs_review) / len(transactions), 3
        )
        if transactions
        else 0.0,
    }


def _cache_size(classifier: Classifier) -> int:
    return getattr(classifier, "size", 0)

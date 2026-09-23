import asyncio
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.classify import Classifier
from app.config import settings
from app.db import get_session
from app.deps import get_classifier, get_cleaner
from app.importers.csv_import import ImportError_, parse_csv
from app.models import CategorySource, Currency, Transaction, Vendor
from app.notes import NoteCleaner
from app.services import vendors as vendor_service
from app.services.recurring import find_patterns
from app.taxonomy import CATEGORIES, REDUCIBILITY_SCALE, is_valid_category

router = APIRouter()

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class ImportSummary(BaseModel):
    batch: str
    imported: int
    auto_categorised: int
    needs_review: int
    from_known_vendors: int = Field(
        description="Categorised from the vendor table with no model call at all."
    )
    classifier_calls_saved: int = Field(
        description="Transactions served from the cache instead of a fresh classifier call."
    )
    vendors_known: int = Field(description="Size of the vendor dataset after this import.")


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
    apply_to_vendor: bool = Field(
        default=True,
        description=(
            "Teach the vendor this category, settling its other unreviewed "
            "transactions and every future one. Set false for a one-off."
        ),
    )


class CorrectionResult(BaseModel):
    transaction: TransactionOut
    learned_for_vendor: str | None
    also_settled: int = Field(
        description="Other transactions this correction settled via the vendor."
    )


class VendorOut(BaseModel):
    id: int
    name: str
    category: str | None
    category_confirmed: bool
    times_seen: int
    total_spent: float
    first_seen: date | None
    last_seen: date | None


class VendorConfirm(BaseModel):
    category: str | None = Field(
        default=None, description="Omit to confirm the provisional category as-is."
    )


class VendorConfirmResult(BaseModel):
    vendor: VendorOut
    settled: int = Field(description="Transactions this confirmation took out of review.")


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
    cleaner: NoteCleaner = Depends(get_cleaner),
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

    # Clean every note first: redact, translate, tidy. Deduplicated, so a
    # statement with thirty grocer lines pays for one cleaning.
    clean_notes = await asyncio.gather(*(cleaner.clean(t.note) for t in transactions))

    # Resolve vendors before classifying, so that anything the household has
    # already confirmed skips the model entirely.
    resolved: list[Vendor | None] = []
    for transaction, note in zip(transactions, clean_notes):
        vendor = vendor_service.resolve(session, note)
        resolved.append(vendor)
        if vendor is not None:
            transaction.vendor_id = vendor.id
            vendor_service.record_sighting(vendor, transaction)
            session.add(vendor)

    needs_model = [
        i for i, vendor in enumerate(resolved) if vendor is None or not vendor.category_confirmed
    ]

    before = _cache_size(classifier)
    results = await asyncio.gather(
        *(
            classifier.classify(
                clean_notes[i].for_classifier,
                transactions[i].amount,
                transactions[i].currency.value,
            )
            for i in needs_model
        ),
        return_exceptions=True,
    )
    decisions = dict(zip(needs_model, results))

    auto = 0
    from_vendors = 0
    for index, transaction in enumerate(transactions):
        vendor = resolved[index]

        if vendor is not None and vendor.category_confirmed:
            transaction.category = vendor.category
            transaction.category_source = CategorySource.RULE
            transaction.confidence = 1.0
            transaction.needs_review = False
            auto += 1
            from_vendors += 1
            continue

        result = decisions.get(index)
        if isinstance(result, BaseException) or result is None:
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
            if vendor is not None and vendor.category is None:
                # A provisional category, so the vendor list is useful straight
                # away and the household can confirm a vendor once instead of
                # answering for each of its transactions. Not marked confirmed:
                # only a person does that.
                vendor.category = result.category
                session.add(vendor)

    session.add_all(transactions)
    session.commit()

    fresh_calls = max(_cache_size(classifier) - before, 0)
    return ImportSummary(
        batch=batch,
        imported=len(transactions),
        auto_categorised=auto,
        needs_review=sum(1 for t in transactions if t.needs_review),
        from_known_vendors=from_vendors,
        classifier_calls_saved=max(len(transactions) - fresh_calls, 0),
        vendors_known=len(list(session.exec(select(Vendor)))),
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


@router.patch("/transactions/{transaction_id}/category", response_model=CorrectionResult)
def set_category(
    transaction_id: int,
    update: CategoryUpdate,
    session: Session = Depends(get_session),
) -> CorrectionResult:
    """The user's correction, and the one place the app learns.

    Marked USER so re-classification never undoes it. If the transaction has a
    vendor, the vendor learns the category too — which settles that vendor's
    other unreviewed transactions now, and every future one without a model call.
    """
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

    also_settled = 0
    learned_for = None
    if update.apply_to_vendor and transaction.vendor_id is not None:
        vendor = session.get(Vendor, transaction.vendor_id)
        if vendor is not None:
            also_settled = vendor_service.learn_category(session, vendor, update.category)
            learned_for = vendor.name

    session.commit()
    session.refresh(transaction)
    return CorrectionResult(
        transaction=TransactionOut.model_validate(transaction, from_attributes=True),
        learned_for_vendor=learned_for,
        also_settled=also_settled,
    )


@router.post("/vendors/{vendor_id}/confirm", response_model=VendorConfirmResult)
def confirm_vendor(
    vendor_id: int,
    update: VendorConfirm,
    session: Session = Depends(get_session),
) -> VendorConfirmResult:
    """Confirm a vendor's category once, for all of its transactions.

    This is the cheaper half of review: one answer about 'Al Amal Supermarket'
    settles its five transactions in this statement and every future one, with
    no model call. Pass a category to override the provisional one.
    """
    vendor = session.get(Vendor, vendor_id)
    if vendor is None:
        raise HTTPException(404, "No such vendor.")

    category = update.category or vendor.category
    if category is None:
        raise HTTPException(
            422, "This vendor has no category to confirm. Supply one explicitly."
        )
    if not is_valid_category(category):
        raise HTTPException(422, f"Unknown category: {category!r}")

    settled = vendor_service.learn_category(session, vendor, category)
    session.commit()
    session.refresh(vendor)
    return VendorConfirmResult(
        vendor=VendorOut.model_validate(vendor, from_attributes=True), settled=settled
    )


@router.get("/vendors", response_model=list[VendorOut])
def list_vendors(
    confirmed_only: bool = False,
    session: Session = Depends(get_session),
) -> list[Vendor]:
    """The vendor dataset the tidying layer builds up, most used first."""
    statement = select(Vendor)
    if confirmed_only:
        statement = statement.where(Vendor.category_confirmed == True)  # noqa: E712
    return list(session.exec(statement.order_by(Vendor.times_seen.desc())))


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

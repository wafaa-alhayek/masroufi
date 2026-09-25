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
from app.models import CategorySource, Currency, Source, Transaction, Vendor
from app.idempotency import remember, replay
from app.notes import NoteCleaner
from app.redact import normalise
from app.services import dedup, transfers
from app.services import vendors as vendor_service
from app.services.recurring import find_patterns
from app import sources as source_service
from app.sources import DEFAULT_SOURCES
from app.taxonomy import CATEGORIES, REDUCIBILITY_SCALE, is_valid_category

router = APIRouter()

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class ImportSummary(BaseModel):
    batch: str
    source: str
    imported: int
    duplicates_skipped: int = Field(
        description="Rows this source already held, so re-importing is safe."
    )
    auto_categorised: int
    needs_review: int
    transfers_linked: int = Field(
        description="Pairs recognised as movements between the household's own accounts."
    )
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
    is_internal_transfer: bool
    source_id: int | None
    vendor_id: int | None


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


class TransferOut(BaseModel):
    out_id: int
    in_id: int
    amount: float
    currency: str
    out_source: str
    in_source: str
    days_apart: int
    prompt: str
    reason: str


class TransferLink(BaseModel):
    out_id: int
    in_id: int


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
    source: str = Query("bop", description="Provider slug; see GET /api/sources."),
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

    provider = source_service.by_slug(session, source)
    if provider is None:
        known = ", ".join(s["slug"] for s in DEFAULT_SOURCES)
        raise HTTPException(422, f"Unknown source {source!r}. Known sources: {known}.")

    batch = uuid.uuid4().hex[:12]
    try:
        parsed = parse_csv(raw, batch=batch, default_currency=currency)
    except ImportError_ as exc:
        raise HTTPException(422, str(exc)) from exc

    for transaction in parsed:
        transaction.source_id = provider.id

    # Drop rows this source already holds, so re-importing an overlapping export
    # does not duplicate a household's history.
    transactions, duplicates = dedup.filter_new(session, parsed)
    if not transactions:
        return ImportSummary(
            batch=batch,
            source=provider.slug,
            imported=0,
            duplicates_skipped=duplicates,
            auto_categorised=0,
            needs_review=0,
            from_known_vendors=0,
            classifier_calls_saved=0,
            vendors_known=len(list(session.exec(select(Vendor)))),
            transfers_linked=0,
        )

    # Clean every note: redact, translate, tidy. Deduplicated, so a statement
    # with thirty grocer lines pays for one cleaning.
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

    # Now that both sides may be present, link up the movements between the
    # household's own accounts so they are not counted as spending.
    linked = transfers.link_confident(session)
    session.commit()

    fresh_calls = max(_cache_size(classifier) - before, 0)
    return ImportSummary(
        batch=batch,
        source=provider.slug,
        imported=len(transactions),
        duplicates_skipped=duplicates,
        auto_categorised=auto,
        needs_review=sum(1 for t in transactions if t.needs_review),
        from_known_vendors=from_vendors,
        classifier_calls_saved=max(len(transactions) - fresh_calls, 0),
        vendors_known=len(list(session.exec(select(Vendor)))),
        transfers_linked=linked,
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
    """Spend by category, plus what is still unreviewed.

    Internal transfers are excluded: a wallet top-up from the bank is the same
    money seen twice, and counting it would inflate spending by however much the
    household moved between its own accounts.
    """
    all_rows = list(session.exec(select(Transaction)))
    spending = [t for t in all_rows if not t.is_internal_transfer]

    by_category: dict[str, float] = {}
    for t in spending:
        if t.amount < 0 and t.category:
            by_category[t.category] = by_category.get(t.category, 0.0) + abs(t.amount)

    return {
        "total_out": round(sum(abs(t.amount) for t in spending if t.amount < 0), 2),
        "total_in": round(sum(t.amount for t in spending if t.amount > 0), 2),
        "internal_transfers_excluded": round(
            sum(abs(t.amount) for t in all_rows if t.is_internal_transfer and t.amount < 0), 2
        ),
        "by_category": {k: round(v, 2) for k, v in sorted(by_category.items(), key=lambda kv: -kv[1])},
        "needs_review": sum(1 for t in spending if t.needs_review),
        "coverage": round(sum(1 for t in spending if not t.needs_review) / len(spending), 3)
        if spending
        else 0.0,
    }


@router.get("/sources")
def list_sources(session: Session = Depends(get_session)) -> list[Source]:
    """The providers a household can import from."""
    return list(session.exec(select(Source)))


@router.get("/transfers/pending", response_model=list[TransferOut])
def pending_transfers(session: Session = Depends(get_session)) -> list[TransferOut]:
    """Suspected movements between the household's own accounts, awaiting an answer.

    Confident pairs are linked during import and do not appear here. These are the
    equal-and-opposite pairs where neither note names the other account, so they
    could equally be two unrelated payments of the same amount.
    """
    return [
        TransferOut(
            out_id=c.out_id,
            in_id=c.in_id,
            amount=c.amount,
            currency=c.currency,
            out_source=c.out_source,
            in_source=c.in_source,
            days_apart=c.days_apart,
            prompt=(
                f"{c.currency} {c.amount:.2f} left {c.out_source} and arrived in "
                f"{c.in_source} {'the same day' if c.days_apart == 0 else f'{c.days_apart} days later'}. "
                "Did you move your own money?"
            ),
            reason=c.reason,
        )
        for c in transfers.find_candidates(session)
        if not c.confident
    ]


@router.post("/transfers/link")
def link_transfer(
    pair: TransferLink,
    session: Session = Depends(get_session),
) -> dict:
    """Confirm that a pair is one movement of the household's own money."""
    debit = session.get(Transaction, pair.out_id)
    credit = session.get(Transaction, pair.in_id)
    if debit is None or credit is None:
        raise HTTPException(404, "One or both transactions do not exist.")
    if debit.amount >= 0 or credit.amount <= 0:
        raise HTTPException(422, "Expected one outgoing and one incoming transaction.")
    if round(debit.amount, 2) != round(-credit.amount, 2):
        raise HTTPException(422, "The two amounts are not equal and opposite.")

    transfers.link(session, debit, credit)
    session.commit()
    return {"linked": True, "amount": abs(debit.amount)}


def _cache_size(classifier: Classifier) -> int:
    return getattr(classifier, "size", 0)


# --- entering a transaction by hand ------------------------------------------


class ManualEntry(BaseModel):
    """One transaction typed in by the household.

    The first way in, not a fallback. A household whose wallet has no export at
    all — which may be most of them — has to be able to use the whole money side
    from day one, and asking them to produce a CSV first would mean they never
    start.
    """

    booked_on: date
    amount: float = Field(
        description="Negative for money out, positive for money in. Zero is refused."
    )
    note: str = Field(min_length=1, description="Shop or person, in their own words.")
    currency: Currency = Currency.ILS
    source: str = Field(
        default="manual", description="Provider slug, for money that came from one."
    )
    category: str | None = Field(
        default=None,
        description="Set when the household already knows. Marked as their own answer "
        "and no model is consulted at all.",
    )
    key: str | None = Field(
        default=None,
        max_length=128,
        description="Client-generated at the moment of tapping, so a replayed offline "
        "queue cannot record the same entry twice.",
    )


class ManualEntryOut(BaseModel):
    transaction: TransactionOut
    vendor_id: int | None
    vendor_name: str | None
    categorised_by: CategorySource
    needs_review: bool
    settled_by_vendor: bool = Field(
        description="True when a vendor the household had already confirmed supplied "
        "the category, so nothing was asked and no model was called."
    )
    transfers_linked: int = 0


@router.post("/transactions", response_model=ManualEntryOut)
async def add_transaction(
    entry: ManualEntry,
    session: Session = Depends(get_session),
    classifier: Classifier = Depends(get_classifier),
    cleaner: NoteCleaner = Depends(get_cleaner),
) -> ManualEntryOut:
    """Record a transaction the household entered themselves.

    It goes through the same path an imported row does — the note is cleaned, the
    vendor resolved and remembered, the category decided — so a manual household
    builds the same vendor dataset and gets the same patterns detected. It is a
    slower-filling way in, not a poorer one, and in one respect a cleaner one:
    there is no bank filler to strip and no parsing to get wrong.

    Deduplication is deliberately **not** applied here. Two identical ₪3 fares
    entered on the same day are two fares, and a household that typed something
    twice on purpose meant it. A double tap is handled by `key` instead.
    """
    seen = replay(session, entry.key, "manual-entry")
    if seen is not None:
        return ManualEntryOut(**seen)

    if entry.amount == 0:
        raise HTTPException(422, "A transaction needs an amount.")
    if entry.category is not None and not is_valid_category(entry.category):
        raise HTTPException(422, f"Unknown category: {entry.category!r}")

    provider = source_service.by_slug(session, entry.source)
    if provider is None:
        known = ", ".join(s["slug"] for s in DEFAULT_SOURCES)
        raise HTTPException(422, f"Unknown source {entry.source!r}. Known: {known}.")

    note = await cleaner.clean(entry.note)
    transaction = Transaction(
        booked_on=entry.booked_on,
        amount=entry.amount,
        currency=entry.currency,
        note=entry.note,
        note_key=note.match_key or normalise(entry.note),
        source_id=provider.id,
        import_batch="manual",
    )

    vendor = vendor_service.resolve(session, note)
    settled_by_vendor = False
    if vendor is not None:
        session.flush()
        transaction.vendor_id = vendor.id
        vendor_service.record_sighting(vendor, transaction)
        session.add(vendor)

    if entry.category is not None:
        # The household said so. Cheapest possible path: no model, no question.
        transaction.category = entry.category
        transaction.category_source = CategorySource.USER
        transaction.confidence = 1.0
        transaction.needs_review = False
    elif vendor is not None and vendor.category_confirmed:
        transaction.category = vendor.category
        transaction.category_source = CategorySource.RULE
        transaction.confidence = 1.0
        transaction.needs_review = False
        settled_by_vendor = True
    else:
        try:
            decision = await classifier.classify(
                note.for_classifier, entry.amount, entry.currency.value
            )
        except Exception:
            # A classifier outage must not lose what someone typed.
            transaction.needs_review = True
        else:
            transaction.category = decision.category
            transaction.confidence = decision.confidence
            transaction.reducibility = decision.reducibility
            transaction.category_source = CategorySource.MODEL
            transaction.needs_review = decision.needs_review(
                settings.confidence_threshold
            )
            if vendor is not None and vendor.category is None and not transaction.needs_review:
                vendor.category = decision.category
                session.add(vendor)

    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    # A hand-entered top-up is exactly as much an internal transfer as an imported
    # one, so the same matching runs.
    linked = transfers.link_confident(session)
    session.commit()
    session.refresh(transaction)

    out = ManualEntryOut(
        transaction=TransactionOut.model_validate(transaction, from_attributes=True),
        vendor_id=vendor.id if vendor else None,
        vendor_name=vendor.name if vendor else None,
        categorised_by=transaction.category_source,
        needs_review=transaction.needs_review,
        settled_by_vendor=settled_by_vendor,
        transfers_linked=linked,
    )
    remember(session, entry.key, "manual-entry", out)
    session.commit()
    return out

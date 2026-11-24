"""The one list of questions, and the answers that only exist here.

The answers that already had a home keep it: a transfer is linked with
`POST /api/transfers/link`, a pattern named with `POST /api/patterns/confirm`, a
shop or a row categorised with `PATCH /api/transactions/{id}/category`. Each
returns `also_settled`. What is new here is saying «لا» to a transfer pair or a
debt match, and saying a payment paid off a debt.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.db import get_session
from app.models import CategorySource, Dismissal, LedgerEntry, LedgerKind, Party, Transaction
from app.routers.debts import PartyOut, party_out
from app.services import questions as question_service

router = APIRouter()


class QuestionOut(BaseModel):
    kind: str = Field(description="transfer | debt_payment | pattern | vendor | transaction")
    key: str = Field(description="Stable while the question stands; what `dismiss` takes.")
    settles: int = Field(description="How many transactions one answer settles. The list is ordered by it.")
    params: dict = Field(description="What the client's template for this kind needs. Never prose.")
    transaction_ids: list[int]


class QuestionsOut(BaseModel):
    count: int
    settles: int = Field(description="Transactions the whole list would settle.")
    questions: list[QuestionOut]


@router.get("", response_model=QuestionsOut)
def list_questions(
    on: date | None = Query(None, description="Today, for what is owed. Defaults to today."),
    session: Session = Depends(get_session),
) -> QuestionsOut:
    qs = question_service.ordered(session, on)
    return QuestionsOut(
        count=len(qs),
        settles=len({tid for q in qs for tid in q.transaction_ids}),
        questions=[
            QuestionOut(kind=q.kind, key=q.key, settles=q.settles, params=q.params, transaction_ids=q.transaction_ids)
            for q in qs
        ],
    )


class DismissIn(BaseModel):
    key: str = Field(max_length=160)


@router.post("/dismiss")
def dismiss(answer: DismissIn, session: Session = Depends(get_session)) -> dict:
    """«لا»: these two are not one transfer, or this payment did not pay that debt.

    Only those two kinds can be turned down. The others always need an answer,
    and leaving them for later is the client's business, not a stored «no».
    Safe to replay.
    """
    if not answer.key.startswith(("transfer:", "debt:")):
        raise HTTPException(422, "Only a transfer pair or a debt match can be turned down.")
    if session.get(Dismissal, answer.key) is None:
        session.add(Dismissal(key=answer.key))
        session.commit()
    return {"dismissed": answer.key, "also_settled": 0}


class DebtPaymentIn(BaseModel):
    transaction_id: int
    party_id: int


class DebtPaymentOut(BaseModel):
    ledger_entry_id: int
    party: PartyOut
    also_settled: int = 1


@router.post("/debt-payment", response_model=DebtPaymentOut)
def paid_a_debt(answer: DebtPaymentIn, session: Session = Depends(get_session)) -> DebtPaymentOut:
    """«إيه»: this payment paid (some of) what is owed to this party.

    The payment becomes `debt_repayment`, the household's own answer, so it
    leaves spending (the goods were counted when they were taken), and a `repay`
    entry tied to it lowers what is owed. Editing or deleting the payment
    follows into that entry. Replaying the same answer changes nothing.
    """
    t = session.get(Transaction, answer.transaction_id)
    party = session.get(Party, answer.party_id)
    if t is None or party is None:
        raise HTTPException(404, "No such transaction or party.")
    if t.amount >= 0:
        raise HTTPException(422, "Only money out can pay a debt.")
    existing = session.exec(select(LedgerEntry).where(LedgerEntry.transaction_id == t.id)).first()
    if existing is not None:
        if existing.party_id == party.id and existing.kind is LedgerKind.REPAY:
            return DebtPaymentOut(ledger_entry_id=existing.id, party=party_out(session, party, date.today()))
        raise HTTPException(409, "This payment is already tied to another debt entry.")

    t.category = "debt_repayment"
    t.category_label = ""
    t.category_source = CategorySource.USER
    t.confidence = 1.0
    t.needs_review = False
    entry = LedgerEntry(
        party_id=party.id,
        kind=LedgerKind.REPAY,
        amount=-t.amount,
        currency=t.currency,
        on=t.booked_on,
        note=t.note,
        transaction_id=t.id,
    )
    session.add(t)
    session.add(entry)
    session.commit()
    return DebtPaymentOut(ledger_entry_id=entry.id, party=party_out(session, party, date.today()))

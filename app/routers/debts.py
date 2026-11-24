"""The debt ledger: «عليكم» and «إلكم».

Shops' tabs and money between people. An entry here is never spending; goods on
the tab are entered as a purchase with `source: "credit"` on
`POST /api/transactions`, which raises the borrow entry in the same write.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.db import get_session
from app.idempotency import remember, replay
from app.models import Currency, LedgerEntry, LedgerKind, Party, PartyKind
from app.services import ledger

router = APIRouter()


class EntryOut(BaseModel):
    id: int
    party_id: int
    kind: LedgerKind
    amount: float
    currency: Currency
    on: date
    due_on: date | None
    note: str
    transaction_id: int | None = Field(description="Set when a transaction raised it (a purchase on the tab, a payment); edit that instead.")
    balance_after: float | None = Field(default=None, description="In this entry's currency. Negative: you owe.")


class PartyOut(BaseModel):
    id: int
    name: str
    kind: PartyKind
    balance: dict[str, float] = Field(description="Per currency. Negative: you owe them; positive: they owe you. Empty: even.")
    last_entry_on: date | None
    due_on: date | None
    overdue: bool


class PartyDetail(PartyOut):
    entries: list[EntryOut]


class DebtsOut(BaseModel):
    we_owe: dict[str, float]
    owed_to_us: dict[str, float]
    parties: list[PartyOut]


def party_out(session: Session, party: Party, today: date) -> PartyOut:
    s = ledger.standing(ledger.entries(session, party.id), today)
    return PartyOut(
        id=party.id,
        name=party.name,
        kind=party.kind,
        balance=s.balance,
        last_entry_on=s.last_entry_on,
        due_on=s.due_on,
        overdue=s.overdue,
    )


def summary(session: Session, today: date, include_settled: bool = False) -> DebtsOut:
    parties = [party_out(session, p, today) for p in session.exec(select(Party))]
    if not include_settled:
        parties = [p for p in parties if p.balance]
    we_owe: dict[str, float] = {}
    owed: dict[str, float] = {}
    for p in parties:
        for cur, v in p.balance.items():
            bucket = owed if v > 0 else we_owe
            bucket[cur] = round(bucket.get(cur, 0.0) + abs(v), 2)
    # A missed date first, then the biggest amounts.
    parties.sort(key=lambda p: (not p.overdue, -max((abs(v) for v in p.balance.values()), default=0)))
    return DebtsOut(we_owe=we_owe, owed_to_us=owed, parties=parties)


@router.get("", response_model=DebtsOut)
def list_debts(
    on: date | None = Query(None, description="Today, for overdue. Defaults to today."),
    include_settled: bool = False,
    session: Session = Depends(get_session),
) -> DebtsOut:
    return summary(session, on or date.today(), include_settled)


@router.get("/parties/{party_id}", response_model=PartyDetail)
def party_detail(party_id: int, on: date | None = None, session: Session = Depends(get_session)) -> PartyDetail:
    party = session.get(Party, party_id)
    if party is None:
        raise HTTPException(404, "No such party.")
    rows = ledger.entries(session, party.id)
    after = ledger.running(rows)
    return PartyDetail(
        **party_out(session, party, on or date.today()).model_dump(),
        entries=[
            EntryOut(**e.model_dump(), balance_after=b) for e, b in zip(rows, after)
        ],
    )


class PartyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    kind: PartyKind | None = None


@router.patch("/parties/{party_id}", response_model=PartyOut)
def change_party(party_id: int, update: PartyUpdate, session: Session = Depends(get_session)) -> PartyOut:
    party = session.get(Party, party_id)
    if party is None:
        raise HTTPException(404, "No such party.")
    if update.name is not None:
        name = update.name.strip()
        clash = session.exec(select(Party).where(Party.name == name, Party.id != party.id)).first()
        if clash is not None:
            raise HTTPException(409, "Another party already has that name.")
        party.name = name
    if update.kind is not None:
        party.kind = update.kind
    session.add(party)
    session.commit()
    return party_out(session, party, date.today())


class EntryIn(BaseModel):
    party_id: int | None = None
    party_name: str | None = Field(default=None, max_length=40, description="Found or added by name.")
    party_kind: PartyKind | None = Field(default=None, description="For a new party. Defaults to person.")
    kind: LedgerKind
    amount: float = Field(gt=0)
    currency: Currency = Currency.ILS
    on: date | None = Field(default=None, description="Defaults to today.")
    due_on: date | None = None
    note: str = Field(default="", max_length=200)
    key: str | None = Field(default=None, max_length=128, description="Made at tap.")


class EntryResult(BaseModel):
    entry: EntryOut
    party: PartyOut


def resolve_party(session: Session, party_id: int | None, name: str | None, kind: PartyKind | None) -> Party:
    if party_id is not None:
        party = session.get(Party, party_id)
        if party is None:
            raise HTTPException(404, "No such party.")
        return party
    if name and name.strip():
        return ledger.find_or_add_party(session, name, kind)
    raise HTTPException(422, "Say who: party_id, or party_name.")


@router.post("/entries", response_model=EntryResult)
def add_entry(entry: EntryIn, session: Session = Depends(get_session)) -> EntryResult:
    """Money borrowed, repaid, lent or collected. Goods on a shop's tab go
    through `POST /api/transactions` with `source: "credit"` instead, so that
    they count as spending."""
    seen = replay(session, entry.key, "ledger-entry")
    if seen is not None:
        return EntryResult(**seen)
    party = resolve_party(session, entry.party_id, entry.party_name, entry.party_kind)
    row = LedgerEntry(
        party_id=party.id,
        kind=entry.kind,
        amount=entry.amount,
        currency=entry.currency,
        on=entry.on or date.today(),
        due_on=entry.due_on,
        note=entry.note.strip(),
    )
    session.add(row)
    session.flush()
    result = EntryResult(entry=EntryOut(**row.model_dump()), party=party_out(session, party, date.today()))
    remember(session, entry.key, "ledger-entry", result)
    session.commit()
    return result


class EntryUpdate(BaseModel):
    amount: float | None = Field(default=None, gt=0)
    on: date | None = None
    due_on: date | None = None
    note: str | None = Field(default=None, max_length=200)


def _owned_by_purchase(row: LedgerEntry) -> None:
    if row.transaction_id is not None:
        raise HTTPException(
            409, "This entry is tied to a transaction (a purchase on the tab, or a payment). Change or remove that instead."
        )


@router.patch("/entries/{entry_id}", response_model=EntryOut)
def change_entry(entry_id: int, update: EntryUpdate, session: Session = Depends(get_session)) -> EntryOut:
    row = session.get(LedgerEntry, entry_id)
    if row is None:
        raise HTTPException(404, "No such entry.")
    if update.amount is not None or update.on is not None:
        _owned_by_purchase(row)
    if update.amount is not None:
        row.amount = update.amount
    if update.on is not None:
        row.on = update.on
    if update.due_on is not None:
        row.due_on = update.due_on
    if update.note is not None:
        row.note = update.note.strip()
    session.add(row)
    session.commit()
    session.refresh(row)
    return EntryOut(**row.model_dump())


@router.delete("/entries/{entry_id}", status_code=204)
def remove_entry(entry_id: int, session: Session = Depends(get_session)) -> Response:
    """404 when already gone, which a replayed queue treats as done."""
    row = session.get(LedgerEntry, entry_id)
    if row is None:
        raise HTTPException(404, "No such entry.")
    _owned_by_purchase(row)
    session.delete(row)
    session.commit()
    return Response(status_code=204)

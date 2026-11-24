"""What the household owes, and is owed.

Balances are worked out from the entries on read, per currency, never stored:
a stored balance drifts the first time an entry is edited. The sign is the
household's: positive means the party owes the household («إلكم»), negative
means the household owes the party («عليكم»).
"""

from dataclasses import dataclass, field
from datetime import date

from sqlmodel import Session, select

from app.models import Currency, LedgerEntry, LedgerKind, Party, PartyKind

SIGN = {
    LedgerKind.LEND: 1,
    LedgerKind.COLLECT: -1,
    LedgerKind.BORROW: -1,
    LedgerKind.REPAY: 1,
}


def signed(entry: LedgerEntry) -> float:
    return SIGN[entry.kind] * entry.amount


def find_or_add_party(session: Session, name: str, kind: PartyKind | None) -> Party:
    name = name.strip()[:40]
    party = session.exec(select(Party).where(Party.name == name)).first()
    if party is None:
        party = Party(name=name, kind=kind or PartyKind.PERSON)
        session.add(party)
        session.flush()
    return party


def entries(session: Session, party_id: int) -> list[LedgerEntry]:
    return list(
        session.exec(
            select(LedgerEntry)
            .where(LedgerEntry.party_id == party_id)
            .order_by(LedgerEntry.on, LedgerEntry.id)
        )
    )


@dataclass
class Standing:
    balance: dict[str, float] = field(default_factory=dict)
    last_entry_on: date | None = None
    due_on: date | None = None
    overdue: bool = False


def standing(rows: list[LedgerEntry], today: date) -> Standing:
    """Balance per currency, and whether a promised date has passed.

    A due date belongs to the latest borrow or lend that set one: a new promise
    replaces an old one. It is overdue once that date has passed with money still
    outstanding.
    """
    s = Standing()
    for e in rows:
        cur = Currency(e.currency).value
        s.balance[cur] = round(s.balance.get(cur, 0.0) + signed(e), 2)
        s.last_entry_on = e.on if s.last_entry_on is None else max(s.last_entry_on, e.on)
        if e.due_on is not None and e.kind in (LedgerKind.BORROW, LedgerKind.LEND):
            s.due_on = e.due_on
    s.balance = {k: v for k, v in s.balance.items() if v != 0}
    s.overdue = bool(s.due_on and s.due_on < today and s.balance)
    return s


def running(rows: list[LedgerEntry]) -> list[float]:
    """The balance after each entry, in that entry's currency."""
    totals: dict[str, float] = {}
    after = []
    for e in rows:
        cur = Currency(e.currency).value
        totals[cur] = round(totals.get(cur, 0.0) + signed(e), 2)
        after.append(totals[cur])
    return after

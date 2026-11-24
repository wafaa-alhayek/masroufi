"""Every question the app has for the household, in one ordered list.

Four places raise questions: transfer pairs, repeat patterns, the review queue
(grouped by shop), and payments that may have paid off a debt. The client used
to merge three endpoints and sort them itself; the order rule lives here
instead: **the answer that settles the most transactions comes first**, ties
broken by kind (a transfer, then a debt, then a pattern, then a shop, then a
single row) and then by the newest date.

A question carries `params`, never prose: the client words it from a fixed
template, so nothing a household reads is generated.
"""

from dataclasses import dataclass, field
from datetime import date

from sqlmodel import Session, select

from app.models import (
    Currency,
    Dismissal,
    LedgerEntry,
    Party,
    Source,
    Transaction,
    Vendor,
)
from app.redact import normalise
from app.services import ledger, transfers
from app.services.recurring import find_patterns

KIND_ORDER = {"transfer": 0, "debt_payment": 1, "pattern": 2, "vendor": 3, "transaction": 4}

# Words that name a kind of shop rather than a shop, so they cannot tie a
# payment to a tab on their own.
GENERIC = {
    "محل", "دكان", "بقالة", "سوبر", "ماركت", "سوبرماركت", "مخبز", "صيدلية", "ابو", "أبو", "ام", "أم",
    "shop", "store", "market", "super", "supermarket", "pos", "payment", "دفع", "تسديد", "دفتر",
}


@dataclass
class Question:
    kind: str
    key: str
    settles: int
    newest: date
    params: dict
    transaction_ids: list[int] = field(default_factory=list)


def _dismissed(session: Session) -> set[str]:
    return {d.key for d in session.exec(select(Dismissal))}


def _words(text: str) -> set[str]:
    return {w for w in normalise(text).split() if len(w) >= 3 and w not in GENERIC}


def debt_key(transaction_id: int, party_id: int) -> str:
    return f"debt:{transaction_id}:{party_id}"


def transfer_key(out_id: int, in_id: int) -> str:
    return f"transfer:{out_id}:{in_id}"


def debt_candidates(session: Session, today: date) -> list[Question]:
    """Money out that names a shop or person the household owes.

    The name has to match (a distinctive word of the party's name in the note or
    the shop's name), the currency has to match, the payment can't be bigger
    than what is owed, and it has to come after the debt began. Amount alone is
    never enough: ₪50 to anyone would match every ₪50 debt.
    """
    owed: list[tuple[Party, dict[str, float], date | None, set[str]]] = []
    for party in session.exec(select(Party)):
        rows = ledger.entries(session, party.id)
        s = ledger.standing(rows, today)
        we_owe = {cur: -v for cur, v in s.balance.items() if v < 0}
        if we_owe:
            first = min((e.on for e in rows), default=None)
            owed.append((party, we_owe, first, _words(party.name)))
    if not owed:
        return []

    on_tab = {s.id for s in session.exec(select(Source)) if s.slug == "credit"}
    linked = {e.transaction_id for e in session.exec(select(LedgerEntry)) if e.transaction_id}
    vendors = {v.id: v.name for v in session.exec(select(Vendor))}
    out = []
    for t in session.exec(select(Transaction).where(Transaction.amount < 0)):
        if (
            t.is_internal_transfer
            or t.excluded
            or t.id in linked
            or t.source_id in on_tab
            or t.category in ("transfer_out", "cash_withdrawal")
        ):
            continue
        text = _words(f"{t.note} {t.note_clean} {vendors.get(t.vendor_id, '')}")
        cur = Currency(t.currency).value
        for party, we_owe, first, name_words in owed:
            if not (name_words & text):
                continue
            if cur not in we_owe or -t.amount > we_owe[cur] + 0.005:
                continue
            if first is not None and t.booked_on < first:
                continue
            out.append(
                Question(
                    kind="debt_payment",
                    key=debt_key(t.id, party.id),
                    settles=1,
                    newest=t.booked_on,
                    params={
                        "transaction_id": t.id,
                        "party_id": party.id,
                        "party_name": party.name,
                        "amount": -t.amount,
                        "currency": cur,
                        "owed": we_owe[cur],
                        "booked_on": t.booked_on.isoformat(),
                    },
                    transaction_ids=[t.id],
                )
            )
    return out


def ordered(session: Session, today: date | None = None) -> list[Question]:
    today = today or date.today()
    dismissed = _dismissed(session)
    rows = list(session.exec(select(Transaction)))
    by_id = {t.id: t for t in rows}
    questions: list[Question] = []
    covered: set[int] = set()

    for c in transfers.find_candidates(session):
        if c.confident or transfer_key(c.out_id, c.in_id) in dismissed:
            continue
        questions.append(
            Question(
                kind="transfer",
                key=transfer_key(c.out_id, c.in_id),
                settles=2,
                newest=max(by_id[c.out_id].booked_on, by_id[c.in_id].booked_on),
                params={
                    "out_id": c.out_id,
                    "in_id": c.in_id,
                    "amount": c.amount,
                    "currency": c.currency,
                    "out_source": c.out_source,
                    "in_source": c.in_source,
                    "days_apart": c.days_apart,
                },
                transaction_ids=[c.out_id, c.in_id],
            )
        )

    questions += [q for q in debt_candidates(session, today) if q.key not in dismissed]

    for p in find_patterns(rows):
        open_ids = [tid for tid in p.transaction_ids if by_id[tid].needs_review]
        if not open_ids:
            continue
        covered.update(open_ids)
        questions.append(
            Question(
                kind="pattern",
                key=f"pattern:{p.note_key}:{round(p.amount, 2)}:{p.currency}",
                settles=len(open_ids),
                newest=p.last_seen,
                params={
                    "note_key": p.note_key,
                    "amount": p.amount,
                    "currency": p.currency,
                    "occurrences": p.occurrences,
                    "median_gap_days": p.median_gap_days,
                    "monthly_estimate": p.monthly_estimate,
                    "by_amount_only": p.by_amount_only,
                },
                transaction_ids=open_ids,
            )
        )

    in_transfer_q = {tid for q in questions if q.kind == "transfer" for tid in q.transaction_ids}
    waiting = [
        t for t in rows
        if t.needs_review and not t.excluded and not t.is_internal_transfer
        and t.id not in covered and t.id not in in_transfer_q
    ]
    by_vendor: dict[int, list[Transaction]] = {}
    singles: list[Transaction] = []
    for t in waiting:
        if t.vendor_id is not None:
            by_vendor.setdefault(t.vendor_id, []).append(t)
        else:
            singles.append(t)
    for vid, group in by_vendor.items():
        vendor = session.get(Vendor, vid)
        latest = max(group, key=lambda t: t.booked_on)
        questions.append(
            Question(
                kind="vendor",
                key=f"vendor:{vid}",
                settles=len(group),
                newest=latest.booked_on,
                params={
                    "vendor_id": vid,
                    "vendor_name": vendor.name if vendor else "",
                    "guess": (vendor.category if vendor else None) or latest.category,
                    "total": round(sum(-t.amount for t in group if t.amount < 0), 2),
                },
                transaction_ids=[t.id for t in group],
            )
        )
    for t in singles:
        questions.append(
            Question(
                kind="transaction",
                key=f"tx:{t.id}",
                settles=1,
                newest=t.booked_on,
                params={
                    "transaction_id": t.id,
                    "note": t.note,
                    "amount": t.amount,
                    "currency": Currency(t.currency).value,
                    "guess": t.category,
                    "booked_on": t.booked_on.isoformat(),
                },
                transaction_ids=[t.id],
            )
        )

    questions.sort(key=lambda q: (-q.settles, KIND_ORDER[q.kind], -q.newest.toordinal()))
    return questions

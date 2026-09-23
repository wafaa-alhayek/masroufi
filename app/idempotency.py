"""Make a replayed write safe.

Every write that happens in a shop happens with no signal: confirming a purchase,
marking something unavailable, taking it from stock, recording a parcel, saying you
have had enough of something. The client queues them and replays when a connection
returns — and a replay that is not recognised as one double-records a purchase,
which means the app lies about what a household spent.

So each of those endpoints accepts a client-generated `key`. The first call does the
work and its response is stored; a repeat of the same key returns that stored
response without doing anything again. The client can therefore retry freely, which
is the only way a queue can be made reliable over a connection that comes and goes.

The key is the client's to generate — a UUID per queued action, created when the
person taps, not when the request is sent. That matters: a key made at send time
would differ between the original and the retry.
"""

import json
from datetime import datetime, timezone

from sqlmodel import Field, Session, SQLModel, select


class WriteOnce(SQLModel, table=True):
    """One completed write, keyed by what the client called it."""

    key: str = Field(primary_key=True, max_length=128)
    endpoint: str = Field(index=True)
    response: str = Field(description="The JSON body the first call returned.")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def replay(session: Session, key: str | None, endpoint: str) -> dict | None:
    """The stored response if this key has already been handled, else None."""
    if not key:
        return None
    row = session.exec(
        select(WriteOnce).where(WriteOnce.key == key, WriteOnce.endpoint == endpoint)
    ).first()
    if row is None:
        return None
    try:
        return json.loads(row.response)
    except ValueError:
        # A corrupt record should not block the write; treat it as unseen.
        return None


def remember(session: Session, key: str | None, endpoint: str, response) -> None:
    """Store what this key produced, so a replay returns the same thing."""
    if not key:
        return
    if session.get(WriteOnce, key) is not None:
        return

    body = response
    if hasattr(response, "model_dump"):
        body = response.model_dump(mode="json")

    session.add(
        WriteOnce(
            key=key,
            endpoint=endpoint,
            response=json.dumps(body, default=str, ensure_ascii=False),
        )
    )

from collections.abc import Iterator

from enum import Enum

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)


def init_db() -> None:
    # Imported for their side effect of registering tables on the metadata.
    from app import idempotency  # noqa: F401

    SQLModel.metadata.create_all(engine)
    add_missing_columns(engine)

    # Imported here rather than at module scope: app.sources imports the models,
    # which must be registered on the metadata above before this runs.
    from app.kitchen import ensure_kitchen
    from app.sources import ensure_sources

    with Session(engine) as session:
        ensure_sources(session)
        ensure_kitchen(session)
        if settings.channel_prices:
            from app.services.prices import load_channel_prices

            load_channel_prices(session)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def _literal(value) -> str:
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def add_missing_columns(bind) -> list[str]:
    """Add columns the models have and an existing database does not.

    `create_all` makes new tables but never changes old ones, so a household
    that installed before a field existed would otherwise crash on its first
    read. Only additions are handled — never a rename, a type change or a drop
    — and every added column takes its model default, so old rows read as they
    always did. Returns what was added, as "table.column".
    """
    inspector = inspect(bind)
    added: list[str] = []
    with bind.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            have = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in have:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" '
                ddl += column.type.compile(dialect=bind.dialect)
                default = column.default.arg if column.default is not None else None
                if default is not None and not callable(default):
                    ddl += " DEFAULT " + _literal(default)
                conn.execute(text(ddl))
                added.append(f"{table.name}.{column.name}")
                # An indexed field gets its index too, or filtering on it scans.
                for index in table.indexes:
                    if column.name in {c.name for c in index.columns}:
                        index.create(conn, checkfirst=True)
    return added

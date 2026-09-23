from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)

    # Imported here rather than at module scope: app.sources imports the models,
    # which must be registered on the metadata above before this runs.
    from app.kitchen import ensure_kitchen
    from app.sources import ensure_sources

    with Session(engine) as session:
        ensure_sources(session)
        ensure_kitchen(session)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session

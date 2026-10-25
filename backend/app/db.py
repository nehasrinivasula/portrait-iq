from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

# check_same_thread=False only matters for SQLite; harmless to leave in.
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def init_db() -> None:
    """Create tables if they don't exist. Fine for the lab; use Alembic
    migrations before this touches a database anyone else depends on."""
    from app import models  # noqa: F401  (import so tables register on Base)
    Base.metadata.create_all(bind=engine)
    _check_schema_is_current()
    _create_missing_indexes()


def _create_missing_indexes() -> None:
    """create_all skips indexes on tables that already exist, so a database
    created before an index was added would never get it. Adding an index
    is safe on live data, unlike adding a column."""
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(bind=engine, checkfirst=True)


def _check_schema_is_current() -> None:
    """create_all never alters existing tables. Fail early and say what to do."""
    from sqlalchemy import inspect
    insp, stale = inspect(engine), []
    for table in Base.metadata.sorted_tables:
        have = {c["name"] for c in insp.get_columns(table.name)}
        stale += [f"{table.name}.{c.name}" for c in table.columns if c.name not in have]
    if stale:
        raise RuntimeError("The database file is out of date (missing: " + ", ".join(stale) +
                           "). It only holds development data: delete portrait_iq.db and restart.")

def get_db():
    """FastAPI dependency: yields a session, always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

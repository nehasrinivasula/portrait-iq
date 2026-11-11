from sqlalchemy import create_engine, inspect, text
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
    _apply_audit_compatibility_columns()
    _check_schema_is_current()
    _create_missing_indexes()


def _apply_audit_compatibility_columns() -> None:
    """Upgrade only known nullable metadata columns in place: SCRUM-36's audit
    columns, S-3's PHI/PII scan columns and the parsed message fields on
    stored events.

    ``create_all`` creates tables but never changes existing ones. These
    columns are nullable, so adding them preserves all existing local SQLite
    rows. Events that predate the scan columns have no scan version, so the
    privacy report counts them as unscanned until a rescan judges them. Other
    future schema changes still fail closed in ``_check_schema_is_current``
    until the project adopts migrations.
    """
    additions = (
        ("audit_log", "session_id", "VARCHAR"),
        ("jobs", "requested_by", "VARCHAR"),
        ("jobs", "session_id", "VARCHAR"),
        ("jobs", "error", "TEXT"),
        ("normalized_events", "privacy_scan_version", "INTEGER"),
        ("normalized_events", "sensitive_flags", "TEXT"),
        ("normalized_events", "network_flags", "TEXT"),
        ("signals", "first_seen", "DATETIME"),
        ("signals", "last_seen", "DATETIME"),
        ("signals", "devices_json", "TEXT"),
        # Message log parsing (ingestion/messages.py). Older events keep NULL:
        # component comes from sourceInfo, which is not stored, so they cannot
        # be backfilled. Pull those logs again to get sequences for them.
        ("normalized_events", "component", "VARCHAR"),
        ("normalized_events", "message_kind", "VARCHAR"),
        ("normalized_events", "command", "VARCHAR"),
        ("normalized_events", "correlation_id", "VARCHAR"),
        ("normalized_events", "status", "VARCHAR"),
    )
    inspector = inspect(engine)
    present = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in {"audit_log", "jobs", "normalized_events", "signals"}
        if inspector.has_table(table)
    }
    with engine.begin() as connection:
        for table, column, sql_type in additions:
            if table in present and column not in present[table]:
                # Table/column/type are module constants, not request data.
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))


def _create_missing_indexes() -> None:
    """create_all skips indexes on tables that already exist, so a database
    created before an index was added would never get it. Adding an index
    is safe on live data, unlike adding a column."""
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(bind=engine, checkfirst=True)


def _check_schema_is_current() -> None:
    """create_all never alters existing tables. Fail early and say what to do."""
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

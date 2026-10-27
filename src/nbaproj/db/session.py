"""Engine and session setup."""

from sqlalchemy import Engine, create_engine, event


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def normalize_database_url(database_url: str) -> str:
    """Return ``database_url`` in the form SQLAlchemy should use.

    Hosted Postgres providers hand out ``postgres://`` or ``postgresql://`` URLs.
    Both are rewritten to name the psycopg driver explicitly.
    """
    for prefix in ("postgres://", "postgresql://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url[len(prefix):]
    return database_url


def create_db_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine for ``database_url``.

    SQLite does not enforce foreign keys unless asked, so they are switched on
    for every SQLite connection.
    """
    engine = create_engine(normalize_database_url(database_url))
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine

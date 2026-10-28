"""Database access for requests: one session per request, closed afterwards.

A handler commits its own work. If it does not (or crashes), closing the session rolls
everything back, so a half-finished request never leaves changes behind.
"""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session


def get_session(request: Request) -> Iterator[Session]:
    """Dependency: a new session on the app's engine, closed when the request ends."""
    engine = request.app.state.engine
    if engine is None:
        raise RuntimeError("this app was built without a database")
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()

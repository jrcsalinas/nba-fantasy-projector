"""Helpers shared by the web tests."""

import re
from datetime import datetime, timedelta
from collections.abc import Iterator, Sequence

from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.routing import Mount

from nbaproj.db.models import JobLock, RefreshLog

from tests.web.conftest import TEST_PASSWORD

CSRF_FIELD = re.compile(r'name="csrf_token" value="([^"]+)"')
OPEN_MOUNTS = frozenset({"/static"})  # holds only the vendored HTMX file, nothing private


def csrf_token(html: str) -> str:
    """The CSRF token in a page's hidden form field."""
    match = CSRF_FIELD.search(html)
    assert match, "the page has no csrf_token field"
    return match.group(1)


def log_in(client, password: str = TEST_PASSWORD):
    """Fetch the login page for its token, then post the password. Redirects are not followed."""
    token = csrf_token(client.get("/login").text)
    return client.post(
        "/login", data={"password": password, "csrf_token": token}, follow_redirects=False
    )


def api_routes(routes: Sequence, prefix: str = "") -> Iterator[tuple[str, str]]:
    """Every (method, path) of the app, walking into included routers.

    FastAPI wraps an included router in a private ``_IncludedRouter`` that exposes
    ``original_router`` and ``include_context.prefix``. Any route type this does not
    recognise fails loudly, so a FastAPI change cannot make a route-by-route check
    silently skip routes. The one open mount (``/static``) is skipped on purpose.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            for method in sorted(route.methods):
                yield method, prefix + route.path
        elif hasattr(route, "original_router") and hasattr(route, "include_context"):
            inner_prefix = prefix + route.include_context.prefix
            yield from api_routes(route.original_router.routes, inner_prefix)
        elif isinstance(route, Mount) and route.path in OPEN_MOUNTS:
            continue
        else:
            raise AssertionError(f"cannot inspect route {route!r}; update api_routes()")


def add_log(
    session: Session,
    now: datetime,
    status: str,
    ago: timedelta,
    message: str | None = None,
) -> None:
    """Add a refresh log row that began ``ago`` before ``now`` (it finished a minute later)."""
    started = now - ago
    finished = None if status == "running" else started + timedelta(minutes=1)
    session.add(
        RefreshLog(
            trigger="cron",
            started_at=started,
            finished_at=finished,
            status=status,
            message=message,
            rows_upserted=0,
        )
    )
    session.commit()


def add_live_run(session: Session, now: datetime, ago: timedelta) -> None:
    """Add a run in progress: a running log row and the unexpired lock a real run holds."""
    add_log(session, now, "running", ago)
    session.add(
        JobLock(
            name="refresh",
            holder="test-run",
            locked_at=now - ago,
            expires_at=now + timedelta(minutes=10),
        )
    )
    session.commit()

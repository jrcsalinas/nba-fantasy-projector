"""``POST /refresh``: start a data refresh, for the cron (bearer token) or for you (login).

Unlike every other unsafe route this one is not behind the login router, because the
cron has no session. It does its own check instead: see ``authorize_refresh``.
"""

import hmac
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Request
from sqlalchemy import exists, select
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse, RedirectResponse, Response

from nbaproj.db.models import JobLock, RefreshLog
from nbaproj.db.refresh_log import STATUS_RUNNING, STATUS_SKIPPED, STATUS_SUCCEEDED
from nbaproj.ingestion.refresh import LOCK_NAME, ingest_final_games
from nbaproj.web.auth import require_login, verify_csrf
from nbaproj.web.db import get_session

logger = logging.getLogger(__name__)

router = APIRouter()

TRIGGER_CRON = "cron"
TRIGGER_UI = "ui"
BEARER_SCHEME = "bearer"

# The minimum time between real runs: a run that is running, succeeded, or skipped this
# recently means a new request starts nothing. A failed run does not count, so the cron's
# retry after a failure is never turned away.
MIN_INTERVAL = timedelta(minutes=10)
RECENT_STATUSES = (STATUS_RUNNING, STATUS_SUCCEEDED, STATUS_SKIPPED)

# The answers /refresh gives. BUSY is what a caller sees as "running": the lock is held.
STARTED, RECENT, BUSY = "started", "recent", "running"
STATUS_CODES = {STARTED: 202, RECENT: 200, BUSY: 409}
MESSAGES = {
    STARTED: "Refresh started.",
    RECENT: "Data was checked a moment ago, so no new refresh was started.",
    BUSY: "A refresh is already running.",
}
FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"


def _bearer_matches(header: str, token: str | None) -> bool:
    """True if ``header`` is ``Bearer <token>`` for the configured token (constant-time compare)."""
    scheme, _, supplied = header.partition(" ")
    if not token or scheme.lower() != BEARER_SCHEME:
        return False
    return hmac.compare_digest(supplied.encode(), token.encode())


async def authorize_refresh(request: Request) -> str:
    """Who is calling, as the refresh trigger: ``cron`` (bearer token) or ``ui`` (login).

    An ``Authorization`` header that is present decides the matter: if it is not the
    right token the answer is a generic 401, even when the request also carries a valid
    session cookie. Without the header the call needs the login and a CSRF token, like
    every other unsafe request. A bearer call needs no CSRF token because it does not
    use cookies.
    """
    header = request.headers.get("Authorization")
    if header is not None:
        if _bearer_matches(header, request.app.state.settings.refresh_token):
            return TRIGGER_CRON
        address = request.client.host if request.client else "unknown"
        logger.warning("Refresh refused: bad bearer token from %r", address)
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"})
    require_login(request)
    await verify_csrf(request)
    return TRIGGER_UI


def decide(session: Session, now: datetime) -> str:
    """What a request to refresh should do now: ``running`` (busy), ``recent``, or ``started``.

    The lock is the shared one, so a laptop run counts as running. Deciding before the run
    means a refused request writes nothing to the refresh log.
    """
    locked = exists().where(JobLock.name == LOCK_NAME, JobLock.expires_at > now)
    if session.scalar(select(locked)):
        return BUSY
    recent = exists().where(
        RefreshLog.status.in_(RECENT_STATUSES), RefreshLog.started_at > now - MIN_INTERVAL
    )
    if session.scalar(select(recent)):
        return RECENT
    return STARTED


def run_refresh(app: FastAPI, trigger: str) -> None:
    """The background run: ingest on its own session. Nothing escapes into the server.

    ``ingest_final_games`` records an unexpected error in the refresh log before it
    raises; this logs the traceback and stops it there.
    """
    try:
        with Session(app.state.engine) as session:
            ingest_final_games(session, app.state.nba_client, trigger, app.state.clock)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Background refresh crashed")
    finally:
        app.state.refresh_in_flight = False


def _is_form_post(request: Request) -> bool:
    return request.headers.get("content-type", "").startswith(FORM_CONTENT_TYPE)


def _answer(request: Request, trigger: str, outcome: str) -> Response:
    """The cron gets JSON with a real status code. You get the banner for an HTMX click
    (always 200, because HTMX does not swap an error response) or a redirect home for a
    plain form post; any other call from a session gets the JSON too.
    """
    if trigger == TRIGGER_UI and request.headers.get("HX-Request"):
        context = {"refresh_message": MESSAGES[outcome], "starting": outcome == STARTED}
        return request.app.state.templates.TemplateResponse(request, "_freshness.html", context)
    if trigger == TRIGGER_UI and _is_form_post(request):
        return RedirectResponse("/", status_code=303)
    return JSONResponse({"status": outcome}, status_code=STATUS_CODES[outcome])


@router.post("/refresh")
def refresh(
    request: Request,
    background: BackgroundTasks,
    trigger: str = Depends(authorize_refresh),
    session: Session = Depends(get_session),
) -> Response:
    """Start a refresh in the background unless one is running or one just ran."""
    state = request.app.state
    # The run takes its lock and writes its log row only after this answer is sent. Until
    # then neither exists, so a second request (a double click) would also decide "started"
    # and cost a rejected run. This process remembers the run it has started; the gate keeps
    # two requests from deciding at the same moment.
    with state.refresh_gate:
        outcome = BUSY if state.refresh_in_flight else decide(session, state.clock())
        if outcome == STARTED:
            state.refresh_in_flight = True
            background.add_task(run_refresh, request.app, trigger)
    return _answer(request, trigger, outcome)


@router.get("/refresh/status", dependencies=[Depends(require_login)])
def refresh_status(request: Request) -> Response:
    """The banner as it is now: what the running banner asks for every three seconds."""
    return request.app.state.templates.TemplateResponse(request, "_freshness.html", {})

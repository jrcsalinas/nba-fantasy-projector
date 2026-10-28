"""The roster pages: the page itself, search, add, remove, and the history.

Every route here needs a login and, for anything that changes data, a CSRF token (the
router's dependencies). Handlers commit their own work; see ``web/db.py``.
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from nbaproj.roster.directory import MIN_QUERY_LENGTH, normalize_name
from nbaproj.roster.service import RosterService, UnknownPlayerError
from nbaproj.web.auth import SESSION_OWNER, csrf_token_for, require_login, verify_csrf
from nbaproj.web.db import get_session

RECENT_CHANGES = 10
HISTORY_LIMIT = 50
# Player ids are 32-bit integers in the database; anything outside is not a player.
MAX_PLAYER_ID = 2**31 - 1

router = APIRouter(dependencies=[Depends(require_login), Depends(verify_csrf)])


def get_roster_service(request: Request, session: Session = Depends(get_session)) -> RosterService:
    """Dependency: the signed-in owner's roster, on this request's database session."""
    return RosterService(session, request.session[SESSION_OWNER], request.app.state.directory)


def _panel_context(request: Request, service: RosterService, message: str = "") -> dict:
    return {
        "players": service.players(),
        "changes": service.changes(limit=RECENT_CHANGES),
        "csrf_token": csrf_token_for(request),
        "message": message,
    }


@router.get("/roster")
def roster_page(
    request: Request, service: RosterService = Depends(get_roster_service)
) -> Response:
    """The roster page: search box, the owner's players, and the recent changes."""
    return request.app.state.templates.TemplateResponse(
        request, "roster.html", _panel_context(request, service)
    )


@router.get("/roster/search")
def roster_search(
    request: Request, q: str = "", service: RosterService = Depends(get_roster_service)
) -> Response:
    """The type-ahead results for ``q``: matches with Add buttons, or a hint."""
    too_short = len(normalize_name(q)) < MIN_QUERY_LENGTH
    matches = [] if too_short else request.app.state.directory.search(q)
    context = {
        "query": q,
        "too_short": too_short,
        "matches": matches,
        "on_roster": {player.player_id for player in service.players()},
        "csrf_token": csrf_token_for(request),
    }
    return request.app.state.templates.TemplateResponse(request, "_search_results.html", context)


def _after_change(request: Request, service: RosterService, message: str) -> Response:
    """HTMX gets the updated panel (and an emptied search area); a plain form gets a redirect."""
    if request.headers.get("HX-Request"):
        return request.app.state.templates.TemplateResponse(
            request, "_panel_response.html", _panel_context(request, service, message)
        )
    return RedirectResponse("/roster", status_code=303)


@router.post("/roster/add")
def roster_add(
    request: Request,
    player_id: int = Form(ge=1, le=MAX_PLAYER_ID),
    service: RosterService = Depends(get_roster_service),
    session: Session = Depends(get_session),
) -> Response:
    """Add a player. Two adds racing each other leave the loser as "already on your roster"."""
    try:
        result = service.add(player_id)
        session.commit()
    except UnknownPlayerError:
        raise HTTPException(status_code=404) from None
    except IntegrityError:
        session.rollback()
        if player_id not in {player.player_id for player in service.players()}:
            raise  # not a duplicate: some other constraint failed, which is a bug
        message = "That player is already on your roster."
    else:
        if result.changed:
            message = f"Added {result.name}."
        else:
            message = f"{result.name} is already on your roster."
    return _after_change(request, service, message)


@router.post("/roster/remove")
def roster_remove(
    request: Request,
    player_id: int = Form(ge=1, le=MAX_PLAYER_ID),
    service: RosterService = Depends(get_roster_service),
    session: Session = Depends(get_session),
) -> Response:
    """Remove a player. Removing someone who is not on the roster is a message, not an error."""
    result = service.remove(player_id)
    session.commit()
    if result.changed:
        message = f"Removed {result.name}."
    else:
        message = "That player was not on your roster."
    return _after_change(request, service, message)


@router.get("/roster/history")
def roster_history(
    request: Request, service: RosterService = Depends(get_roster_service)
) -> Response:
    """The owner's last ``HISTORY_LIMIT`` roster changes, newest first."""
    context = {"changes": service.changes(limit=HISTORY_LIMIT), "limit": HISTORY_LIMIT}
    return request.app.state.templates.TemplateResponse(request, "history.html", context)

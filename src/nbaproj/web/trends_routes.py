"""The trends page: how each player on the roster is doing lately against his own season."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from nbaproj.roster.service import RosterService
from nbaproj.services.trends import load_trends
from nbaproj.web.auth import require_login, verify_csrf
from nbaproj.web.db import get_session
from nbaproj.web.roster_routes import get_roster_service
from nbaproj.web.trend_cards import build_cards

WINDOWS = (7, 14, 30)
DEFAULT_WINDOW = 14
DATE_FORMAT = "%b %d, %Y"

router = APIRouter(dependencies=[Depends(require_login), Depends(verify_csrf)])


@router.get("/trends")
def trends_page(
    request: Request,
    window: int = DEFAULT_WINDOW,
    roster: RosterService = Depends(get_roster_service),
    session: Session = Depends(get_session),
) -> Response:
    """The roster's trends over the last ``window`` days (7, 14 or 30; anything else is a 400)."""
    if window not in WINDOWS:
        raise HTTPException(status_code=400)
    view = load_trends(session, roster, request.app.state.settings.timezone, window)
    context = {
        "view": view,
        "cards": build_cards(view),
        "windows": WINDOWS,
        "data_through": view.last_game_day.strftime(DATE_FORMAT) if view.last_game_day else "",
    }
    return request.app.state.templates.TemplateResponse(request, "trends.html", context)

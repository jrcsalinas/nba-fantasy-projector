"""``POST /refresh``: start a data refresh, for the cron (bearer token) or for you (login).

Unlike every other unsafe route this one is not behind the login router, because the
cron has no session. It does its own check instead: see ``authorize_refresh``.
"""

import hmac
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import JSONResponse

from nbaproj.web.auth import require_login, verify_csrf

router = APIRouter()

TRIGGER_CRON = "cron"
TRIGGER_UI = "ui"
BEARER_SCHEME = "bearer"


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
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"})
    require_login(request)
    await verify_csrf(request)
    return TRIGGER_UI


@router.post("/refresh", status_code=202, dependencies=[Depends(authorize_refresh)])
def refresh() -> JSONResponse:
    """Accept a refresh request from an authorized caller (it does not start a run yet)."""
    return JSONResponse({"status": "accepted"}, status_code=202)

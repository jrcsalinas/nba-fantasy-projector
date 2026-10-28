"""Login, logout, the signed-in check, and CSRF protection.

The session is a signed cookie holding only the owner's name and the CSRF token. Logging
in clears the old session and issues a new token, so nothing from before carries over.
"""

import hmac
import logging
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse
from starlette.templating import Jinja2Templates

from nbaproj.web.security import LoginLimiter, verify_password

logger = logging.getLogger(__name__)

SESSION_OWNER = "owner"
SESSION_CSRF = "csrf"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "X-CSRF-Token"
CSRF_FIELD = "csrf_token"
LOGIN_ERROR = "That password is not right."
TOKEN_BYTES = 32


class NotSignedIn(Exception):
    """Raised for a request that needs a login and has none."""


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


def csrf_token_for(request: Request) -> str:
    """The session's CSRF token, made on first use."""
    token = request.session.get(SESSION_CSRF)
    if not token:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        request.session[SESSION_CSRF] = token
    return token


def require_login(request: Request) -> None:
    """Dependency: stop the request unless the session names the owner."""
    if not request.session.get(SESSION_OWNER):
        raise NotSignedIn


async def verify_csrf(request: Request) -> None:
    """Dependency: a request that can change something must carry the session's token.

    The token comes in the ``X-CSRF-Token`` header (HTMX) or the ``csrf_token`` form
    field (plain forms) and is compared in constant time.
    """
    if request.method in SAFE_METHODS:
        return
    expected = request.session.get(SESSION_CSRF)
    submitted = request.headers.get(CSRF_HEADER)
    if submitted is None:
        submitted = (await request.form()).get(CSRF_FIELD)
    if not (
        isinstance(expected, str)
        and isinstance(submitted, str)
        and hmac.compare_digest(expected.encode(), submitted.encode())
    ):
        raise HTTPException(status_code=403)


def _login_page(request: Request, status_code: int = 200, error: str | None = None) -> Response:
    context = {"csrf_token": csrf_token_for(request), "error": error}
    return _templates(request).TemplateResponse(
        request, "login.html", context, status_code=status_code
    )


# Routes anyone may reach.
open_router = APIRouter()


@open_router.get("/login")
async def login_page(request: Request) -> Response:
    """Show the login form, or go home if already signed in."""
    if request.session.get(SESSION_OWNER):
        return RedirectResponse("/", status_code=303)
    return _login_page(request)


@open_router.post("/login", dependencies=[Depends(verify_csrf)])
async def login(request: Request, password: str = Form("")) -> Response:
    """Check the password: one generic error for any failure, and 429 for a locked-out address."""
    settings = request.app.state.settings
    limiter: LoginLimiter = request.app.state.login_limiter
    address = request.client.host if request.client else "unknown"
    wait = limiter.retry_after(address)
    if wait:
        logger.warning("Login refused for locked-out address %r", address)
        raise HTTPException(status_code=429, headers={"Retry-After": str(wait)})
    # The guess is counted before the slow check, with no await in between: guesses that
    # arrive together are counted together, so only five can be checked per lockout. A
    # correct password clears the count again. (Counting after the check would let every
    # in-flight guess through, because they all pass the lockout test before any failure lands.)
    limiter.record_failure(address)
    # Argon2 is deliberately slow and memory-hungry: in a worker thread it cannot stall the loop.
    if not await run_in_threadpool(verify_password, settings.password_hash, password):
        logger.warning("Failed login from %r", address)
        return _login_page(request, status_code=401, error=LOGIN_ERROR)
    limiter.record_success(address)
    request.session.clear()
    request.session[SESSION_OWNER] = settings.owner_name
    request.session[SESSION_CSRF] = secrets.token_urlsafe(TOKEN_BYTES)
    return RedirectResponse("/", status_code=303)


# Routes that need a login. Later stages add their routes here.
# The login check comes first, so a visitor with no session is sent to /login, not told 403.
protected_router = APIRouter(dependencies=[Depends(require_login), Depends(verify_csrf)])


@protected_router.get("/")
async def home(request: Request) -> Response:
    """The signed-in start page."""
    context = {"owner": request.session[SESSION_OWNER], "csrf_token": csrf_token_for(request)}
    return _templates(request).TemplateResponse(request, "home.html", context)


@protected_router.post("/logout")
async def logout(request: Request) -> Response:
    """End the session."""
    request.session.clear()
    return RedirectResponse("/login", status_code=303)

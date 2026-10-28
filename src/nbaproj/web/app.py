"""The FastAPI app factory: sessions, login, security headers, and generic error pages."""

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse
from starlette.templating import Jinja2Templates

from nbaproj.web.auth import NotSignedIn, open_router, protected_router
from nbaproj.web.security import LoginLimiter
from nbaproj.web.settings import WebSettings, load_web_settings

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}
# What an error page may say. Never the request path, an exception message, or a detail.
ERROR_PAGES = {
    401: ("Sign in needed", "Please sign in to continue."),
    403: ("Not allowed", "That request was not allowed."),
    404: ("Page not found", "There is nothing at this address."),
    405: ("Not allowed", "That request is not allowed here."),
    429: ("Too many attempts", "Please wait a while and try again."),
}
GENERIC_ERROR_PAGE = ("Something went wrong", "Please try again in a moment.")
SESSION_MAX_AGE_SECONDS = 7 * 24 * 3600


def apply_security_headers(response: Response) -> Response:
    """Add the security headers every response carries."""
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    return response


def _error_page(request: Request, status_code: int) -> Response:
    title, message = ERROR_PAGES.get(status_code, GENERIC_ERROR_PAGE)
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request, "error.html", {"title": title, "message": message}, status_code=status_code
    )


def create_app(settings: WebSettings, limiter: LoginLimiter | None = None) -> FastAPI:
    """Build the app. The API documentation pages are off: nothing describes the app.

    ``limiter`` is the login lockout; tests pass one with a fake clock.
    """
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.login_limiter = limiter or LoginLimiter()
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        return apply_security_headers(await call_next(request))

    # Added after the header middleware, so it wraps it and request.session is ready there.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        max_age=SESSION_MAX_AGE_SECONDS,
        same_site="lax",
        https_only=settings.cookie_secure,
    )

    @app.exception_handler(NotSignedIn)
    async def not_signed_in(request: Request, _exc: NotSignedIn) -> Response:
        if request.headers.get("HX-Request"):
            return _error_page(request, 401)
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        response = _error_page(request, exc.status_code)
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception) -> Response:
        # Starlette runs this outside the middleware above, so it adds the headers itself.
        logger.error("Unhandled error on %s", request.url.path, exc_info=exc)
        return apply_security_headers(_error_page(request, 500))

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(open_router)
    app.include_router(protected_router)
    return app


def create_app_from_env() -> FastAPI:
    """Build the app from the process environment: the factory ``uvicorn --factory`` calls.

    Raises ``ConfigError`` (naming the variable, never its value) if a secret is missing.
    """
    return create_app(load_web_settings(os.environ))

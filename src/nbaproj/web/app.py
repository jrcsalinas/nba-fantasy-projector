"""The FastAPI app factory: sessions, login, security headers, and generic error pages."""

import logging
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from nbaproj.config import ConfigError, load_settings
from nbaproj.db.session import create_db_engine
from nbaproj.roster.directory import PlayerDirectory, default_directory
from nbaproj.ingestion.runs import utc_now
from nbaproj.web.auth import (
    SESSION_OWNER,
    NotSignedIn,
    csrf_token_for,
    open_router,
    protected_router,
)
from nbaproj.web.freshness import load_freshness
from nbaproj.web.refresh_routes import router as refresh_router
from nbaproj.web.roster_routes import router as roster_router
from nbaproj.web.security import LoginLimiter
from nbaproj.web.settings import WebSettings, load_web_settings

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}
# What an error page may say. Never the request path, an exception message, or a detail.
ERROR_PAGES = {
    400: ("Request not valid", "That request was not valid."),
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


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """At shutdown, dispose the engine, but only one the app made itself."""
    yield
    if app.state.owns_engine and app.state.engine is not None:
        app.state.engine.dispose()


def _page_context(request: Request) -> dict:
    """Template context for every page: the signed-in state, CSRF token, and the banner.

    A page never fails because the banner could not be read: with no database, or one
    that errors, the banner is simply left out.
    """
    if not request.session.get(SESSION_OWNER):
        return {"signed_in": False, "freshness": None}
    context = {"signed_in": True, "csrf_token": csrf_token_for(request), "freshness": None}
    engine = request.app.state.engine
    if engine is not None:
        try:
            with Session(engine) as session:
                context["freshness"] = load_freshness(session, request.app.state.clock())
        except SQLAlchemyError:
            logger.exception("Could not read the refresh log for the banner")
    return context


def _local_time(zone_name: str) -> Callable[[datetime], str]:
    """A template filter that shows a stored UTC time in the configured timezone."""
    zone = ZoneInfo(zone_name)

    def local_time(moment: datetime) -> str:
        return moment.astimezone(zone).strftime("%b %d, %Y %H:%M %Z")

    return local_time


def create_app(
    settings: WebSettings,
    limiter: LoginLimiter | None = None,
    engine: Engine | None = None,
    directory: PlayerDirectory | None = None,
) -> FastAPI:
    """Build the app. The API documentation pages are off: nothing describes the app.

    ``limiter`` is the login lockout, ``engine`` the database, and ``directory`` the players
    that can be found by name; tests pass their own. An engine passed in is the caller's to
    dispose.
    """
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.owns_engine = False
    app.state.directory = directory if directory is not None else default_directory()
    app.state.login_limiter = limiter or LoginLimiter()
    app.state.clock = utc_now  # tests replace it to fix the time
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR), context_processors=[_page_context])
    templates.env.filters["local_time"] = _local_time(settings.timezone)
    app.state.templates = templates

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

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, _exc: RequestValidationError) -> Response:
        # FastAPI's own answer lists field names and rules, which describe the app.
        return _error_page(request, 400)

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
    app.include_router(roster_router)
    app.include_router(refresh_router)
    # Open on purpose: it holds only the vendored HTMX file and its license.
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


def create_app_from_env() -> FastAPI:
    """Build the app from the process environment: the factory ``uvicorn --factory`` calls.

    The database is the one ``DATABASE_URL`` names, and the app disposes its engine at
    shutdown. Raises ``ConfigError`` (naming the variable, never its value) if a secret is
    missing or the database URL cannot be used.
    """
    web_settings = load_web_settings(os.environ)
    database_url = load_settings(os.environ).database_url
    try:
        engine = create_db_engine(database_url)
    except (SQLAlchemyError, ImportError):
        raise ConfigError("DATABASE_URL is not a usable database URL") from None
    app = create_app(web_settings, engine=engine)
    app.state.owns_engine = True
    return app

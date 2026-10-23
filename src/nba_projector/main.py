"""FastAPI application entry point."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from nba_projector.api.roster_routes import router as roster_router
from nba_projector.api.routes import router

WEB_DIR = Path(__file__).parent / "web"

app = FastAPI(
    title="NBA Fantasy Projector",
    version="0.1.0",
    description="Projections are estimates, not guarantees.",
)
app.include_router(router)
app.include_router(roster_router)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    """Serve the rankings dashboard page."""
    return FileResponse(WEB_DIR / "index.html")

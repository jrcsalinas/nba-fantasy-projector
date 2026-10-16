"""FastAPI application entry point."""

from fastapi import FastAPI

from nba_projector.api.routes import router

app = FastAPI(
    title="NBA Fantasy Projector",
    version="0.1.0",
    description="Projections are estimates, not guarantees.",
)
app.include_router(router)

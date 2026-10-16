"""Version 1 API routes."""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1")


class HelloResponse(BaseModel):
    """Response body for the hello endpoint."""

    message: str


@router.get("/hello", response_model=HelloResponse, summary="Hello world")
def hello() -> HelloResponse:
    """Return a greeting to confirm the API is running."""
    return HelloResponse(message="Hello, NBA Fantasy!")

"""FastAPI dependencies shared by the API routers."""

from datetime import date, datetime
from functools import lru_cache

from fastapi import HTTPException

from nba_projector.adapters.repository import Repository
from nba_projector.adapters.roster_file import (
    RosterFileError,
    RosterFileNotFoundError,
    load_roster,
)
from nba_projector.config import EASTERN, get_settings
from nba_projector.domain.roster import Roster


@lru_cache
def get_repository() -> Repository:
    """Dependency: the repository for the configured database."""
    return Repository(get_settings().db_path)


def get_today() -> date:
    """Dependency: today's date in US Eastern time (overridable in tests)."""
    return datetime.now(EASTERN).date()


def get_roster() -> Roster:
    """Dependency: my roster, re-read from disk on every request so edits apply immediately."""
    path = get_settings().roster_path
    try:
        return load_roster(path)
    except RosterFileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Add {path.as_posix()}") from exc
    except RosterFileError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

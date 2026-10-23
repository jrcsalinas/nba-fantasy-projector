"""Load my roster from a local JSON file.

Expected shape::

    {"active": [{"player_id": 1630178, "name": "Tyrese Maxey"}],
     "il": [{"player_id": 1629632, "name": "Coby White"}]}

``il`` is optional. Players are matched by ``player_id``; ``name`` is only for readability.
"""

import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from nba_projector.domain.roster import Roster, RosterPlayer


class RosterFileError(Exception):
    """The roster file exists but cannot be used."""


class RosterFileNotFoundError(RosterFileError):
    """The roster file does not exist."""


class _PlayerEntry(BaseModel):
    player_id: int
    name: str


class _RosterFile(BaseModel):
    active: list[_PlayerEntry]
    il: list[_PlayerEntry] = Field(default_factory=list)


def load_roster(path: Path) -> Roster:
    """Read and validate the roster file.

    Args:
        path: Location of the roster JSON file.

    Returns:
        The roster.

    Raises:
        RosterFileNotFoundError: If the file does not exist.
        RosterFileError: If it is not valid JSON, has the wrong shape, or lists a player twice.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RosterFileNotFoundError(f"Roster file not found: {path}") from exc

    try:
        parsed = _RosterFile.model_validate(json.loads(text))
    except json.JSONDecodeError as exc:
        raise RosterFileError(f"Roster file {path} is not valid JSON: {exc}") from exc
    except ValidationError as exc:
        raise RosterFileError(f"Roster file {path} has the wrong shape: {exc}") from exc

    active = tuple(RosterPlayer(e.player_id, e.name) for e in parsed.active)
    il = tuple(RosterPlayer(e.player_id, e.name) for e in parsed.il)
    ids = [p.player_id for p in (*active, *il)]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise RosterFileError(f"Roster file {path} lists player_id more than once: {duplicates}")
    return Roster(active=active, il=il)

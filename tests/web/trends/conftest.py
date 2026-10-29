"""Fixtures for the trends page tests: seeded games, a roster, and signed-in clients."""

import pytest
from sqlalchemy.orm import Session

from nbaproj.roster.directory import PlayerDirectory
from nbaproj.roster.service import RosterService
from tests.services.conftest import NAMES, Seeder

# The client factory (and its fake NBA client and fixed clock) is the refresh tests' one: it is
# imported so that pytest finds it here, and it uses this folder's ``directory`` below.
from tests.web.refresh.conftest import make_client_fixture  # pylint: disable=unused-import

EVIL = (5, "<b>Evil</b> Name")
DIRECTORY = PlayerDirectory([*NAMES.items(), EVIL])


@pytest.fixture(name="seed")
def seed_fixture(engine):
    """A seeder on the test database (games and box score lines, players 1 to 4)."""
    with Session(engine) as session:
        yield Seeder(session)


@pytest.fixture(name="roster_of")
def roster_of_fixture(engine):
    """``roster_of("josh", 1, 4)`` puts those players on that owner's roster."""

    def put(owner: str, *player_ids: int) -> None:
        with Session(engine) as session:
            service = RosterService(session, owner, DIRECTORY)
            for player_id in player_ids:
                service.add(player_id)
            session.commit()

    return put


@pytest.fixture(name="directory")
def directory_fixture() -> PlayerDirectory:
    """This folder's players: the imported client factory asks for ``directory``."""
    return DIRECTORY

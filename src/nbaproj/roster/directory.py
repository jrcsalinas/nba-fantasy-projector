"""Finding players by name in nba_api's offline list of active players.

Names and queries are compared as lowercase ASCII words, so "doncic" finds Luka
Dončić and "de'aaron" finds De'Aaron Fox. Every typed word must start a word of the
name; nicknames are not searched.
"""

import functools
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from nba_api.stats.static import players as nba_players

MIN_QUERY_LENGTH = 2
DEFAULT_LIMIT = 10


def normalize_name(name: str) -> str:
    """Lowercase, fold accents to base letters, and turn punctuation into single spaces."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", folded.lower()).strip()


@dataclass(frozen=True)
class PlayerMatch:
    """A player the directory knows: the NBA player id and the display name."""

    player_id: int
    name: str


class PlayerDirectory:
    """A searchable list of players, held in memory."""

    def __init__(self, players: Iterable[tuple[int, str]]):
        self._entries = [
            (PlayerMatch(player_id, name), normalize_name(name)) for player_id, name in players
        ]
        self._by_id = {match.player_id: match for match, _ in self._entries}

    def all_players(self) -> list[PlayerMatch]:
        """Every player in the directory."""
        return [match for match, _ in self._entries]

    def get(self, player_id: int) -> PlayerMatch | None:
        """The player with this NBA id, or None."""
        return self._by_id.get(player_id)

    def search(self, query: str, limit: int = DEFAULT_LIMIT) -> list[PlayerMatch]:
        """Players whose name has a word starting each typed word, best matches first.

        Names that start with the whole query come first, then the rest alphabetically.
        A query shorter than two characters finds nothing.
        """
        normalized = normalize_name(query)
        if len(normalized) < MIN_QUERY_LENGTH:
            return []
        typed = normalized.split()
        hits = [
            (match, key)
            for match, key in self._entries
            if all(any(word.startswith(part) for word in key.split()) for part in typed)
        ]
        hits.sort(key=lambda hit: (not hit[1].startswith(normalized), hit[1]))
        return [match for match, _ in hits[:limit]]


@functools.cache
def default_directory() -> PlayerDirectory:
    """The directory of the league's active players, from nba_api's offline data."""
    return PlayerDirectory(
        (player["id"], player["full_name"]) for player in nba_players.get_active_players()
    )

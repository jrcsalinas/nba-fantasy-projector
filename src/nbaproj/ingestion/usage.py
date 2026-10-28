"""Usage rate, derived from box score totals (the live feed has no usage field)."""

from dataclasses import dataclass

FTA_WEIGHT = 0.44
PLAYERS_ON_COURT = 5


@dataclass(frozen=True)
class Totals:
    """The box score totals the usage formula needs, for a player or a team."""

    minutes: float
    fga: int
    fta: int
    tov: int

    @property
    def possessions_used(self) -> float:
        """Field goal attempts, weighted free throw attempts, and turnovers."""
        return self.fga + FTA_WEIGHT * self.fta + self.tov


def usage_pct(player: Totals, team: Totals) -> float | None:
    """Share of team possessions a player used while on the floor, as a percentage.

    Returns None when the player has no minutes or the team used no possessions.
    """
    if player.minutes <= 0 or team.possessions_used <= 0:
        return None
    team_minutes_per_player = team.minutes / PLAYERS_ON_COURT
    return (
        100
        * player.possessions_used
        * team_minutes_per_player
        / (player.minutes * team.possessions_used)
    )

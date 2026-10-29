"""Turning a trends view into the text the page shows, so the template stays plain."""

from dataclasses import dataclass

from nbaproj.scoring.categories import CATEGORIES
from nbaproj.scoring.trends import COLD, HOT, MIN_MOVER_Z, PERCENTAGE_KEYS, CategoryTrend
from nbaproj.services.trends import TrendRow, TrendsView

UP, DOWN, FLAT = "up", "down", "flat"
NOT_KNOWN = "n/a"


@dataclass(frozen=True)
class Cell:
    """One category on a card: its label, the plain change, the z change, and its tone."""

    label: str
    change: str
    z_change: str
    tone: str


@dataclass(frozen=True)
class Card:
    """One roster player on the page. A player without a trend has only a name and a reason."""

    name: str
    has_trend: bool
    reason: str
    tone: str = FLAT
    label: str = ""
    overall: str = ""
    games: str = ""
    minutes: str = ""
    usage: str = ""
    low_sample: bool = False
    cells: tuple[Cell, ...] = ()


def _tone(z_change: float) -> str:
    if z_change >= MIN_MOVER_Z:
        return UP
    if z_change <= -MIN_MOVER_Z:
        return DOWN
    return FLAT


def _games_text(count: int) -> str:
    return f"{count} game" if count == 1 else f"{count} games"


def _change_text(category: CategoryTrend) -> str:
    if category.change is None:
        return NOT_KNOWN
    if category.key in PERCENTAGE_KEYS:
        return f"{category.change * 100:+.1f} pts"
    return f"{category.change:+.1f}"


def _label_tone(label: str) -> str:
    return {HOT: UP, COLD: DOWN}.get(label, FLAT)


def _card(row: TrendRow, window_days: int) -> Card:
    trend = row.trend
    if not trend.has_trend:
        return Card(row.name, has_trend=False, reason=trend.reason)
    usage = (
        "Usage unknown"
        if trend.usage_change is None
        else f"Usage {trend.usage_change:+.1f} pts"
    )
    by_key = {category.key: category for category in trend.categories}
    return Card(
        name=row.name,
        has_trend=True,
        reason=trend.reason,
        tone=_label_tone(trend.label or ""),
        label=trend.label or "",
        overall=f"{trend.overall or 0.0:+.1f}",
        games=(
            f"{_games_text(trend.window_games)} in the last {window_days} days"
            f" · {trend.season_games} this season"
        ),
        minutes=f"Minutes {trend.minutes_change or 0.0:+.1f}",
        usage=usage,
        low_sample=trend.low_sample,
        cells=tuple(
            Cell(
                label=c.label,
                change=_change_text(by_key[c.key]),
                z_change=f"{by_key[c.key].z_change:+.1f}",
                tone=_tone(by_key[c.key].z_change),
            )
            for c in CATEGORIES
        ),
    )


def build_cards(view: TrendsView) -> list[Card]:
    """One card per roster player, in the order of the view (alphabetical)."""
    return [_card(row, view.window_days) for row in view.rows]

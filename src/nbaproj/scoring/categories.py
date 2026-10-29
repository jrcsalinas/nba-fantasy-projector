"""The nine scoring categories of the league, in display order."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    """One category: where its value comes from and how it reads.

    ``stat`` is the period-stats column that is z-scored. ``shown`` is the column that
    reads naturally in a sentence (for FG% that is the plain percentage, while the z-score
    uses impact, which weighs volume). ``negated`` is for categories where less is better.
    """

    key: str
    label: str
    stat: str
    shown: str
    negated: bool = False


def _counting(key: str, label: str, negated: bool = False) -> Category:
    return Category(key, label, stat=key, shown=key, negated=negated)


CATEGORIES = (
    _counting("pts", "PTS"),
    _counting("reb", "REB"),
    _counting("ast", "AST"),
    _counting("stl", "STL"),
    _counting("blk", "BLK"),
    _counting("tpm", "3PM"),
    _counting("tov", "TO", negated=True),
    Category("fg", "FG%", stat="fg_impact", shown="fg_pct"),
    Category("ft", "FT%", stat="ft_impact", shown="ft_pct"),
)

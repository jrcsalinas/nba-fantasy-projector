"""Population z-scores: how far above or below the average player, in each category."""

import pandas as pd

from nbaproj.scoring.categories import CATEGORIES


def z_scores(stats: pd.DataFrame, min_games: int) -> pd.DataFrame:
    """A z-score per player per category (columns are the category keys).

    The population is every player in ``stats`` with at least ``min_games`` games; its
    mean and standard deviation (the population one, dividing by n) define the scale.
    Players under ``min_games`` get NaN in every column and are left out of the
    population, so one-game flukes do not shift anyone else. A category in which nobody
    varies scores 0. Categories where less is better (turnovers) are negated, so a
    positive score is always good. The input is not modified.
    """
    scored = stats[stats["games"] >= min_games]
    z = pd.DataFrame(index=stats.index, columns=[c.key for c in CATEGORIES], dtype=float)
    for category in CATEGORIES:
        values = scored[category.stat]
        spread = values.std(ddof=0)
        score = (values - values.mean()) / spread if spread > 0 else values * 0.0
        z.loc[scored.index, category.key] = -score if category.negated else score
    return z

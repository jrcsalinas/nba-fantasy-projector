from nba_projector.domain.models import SORT_KEYS, Category, RankedPlayer
from nba_projector.domain.roster import team_totals, waiver_candidates, weakest_categories


def make_player(
    player_id: int, overall: int, z: dict[Category, float] | None = None
) -> RankedPlayer:
    values = {cat: 0.0 for cat in Category}
    values.update(z or {})
    return RankedPlayer(
        player_id=player_id,
        player_name=f"Player {player_id}",
        z=values,
        total=sum(values.values()),
        ranks={key: overall for key in SORT_KEYS},
    )


def test_team_totals_sums_z_per_category() -> None:
    players = [
        make_player(1, 1, {Category.PTS: 2.0, Category.TOV: -1.0}),
        make_player(2, 2, {Category.PTS: 1.5, Category.TOV: 0.5}),
    ]

    totals = team_totals(players)

    assert totals[Category.PTS] == 3.5
    assert totals[Category.TOV] == -0.5
    assert set(totals) == set(Category)


def test_team_totals_empty_roster_is_all_zero() -> None:
    assert team_totals([]) == {cat: 0.0 for cat in Category}


def test_weakest_categories_returns_lowest_sums_first() -> None:
    totals = {cat: 1.0 for cat in Category}
    totals[Category.BLK] = -3.0
    totals[Category.FT_PCT] = -1.0
    totals[Category.AST] = 0.0

    assert weakest_categories(totals, 3) == [Category.BLK, Category.FT_PCT, Category.AST]


def test_weakest_categories_breaks_ties_in_category_order() -> None:
    totals = {cat: 0.0 for cat in Category}

    assert weakest_categories(totals, 2) == [Category.PTS, Category.REB]


def test_waiver_candidates_excludes_roster_and_players_inside_cutoff() -> None:
    snapshot = [
        make_player(1, 10, {Category.BLK: 5.0}),  # inside cutoff: likely rostered
        make_player(2, 160, {Category.BLK: 2.0}),  # candidate
        make_player(3, 170, {Category.BLK: 3.0}),  # on my roster
        make_player(4, 180, {Category.BLK: 1.0}),  # candidate
    ]

    result = waiver_candidates(snapshot, {3}, Category.BLK, cutoff=150, limit=5)

    assert [p.player_id for p in result] == [2, 4]


def test_waiver_candidates_orders_by_category_z_then_overall_rank() -> None:
    snapshot = [
        make_player(1, 200, {Category.STL: 1.0}),
        make_player(2, 160, {Category.STL: 1.0}),
        make_player(3, 190, {Category.STL: 2.5}),
    ]

    result = waiver_candidates(snapshot, set(), Category.STL, cutoff=150, limit=5)

    assert [p.player_id for p in result] == [3, 2, 1]


def test_waiver_candidates_cutoff_is_exclusive_and_limit_applies() -> None:
    snapshot = [make_player(i, 150 + i, {Category.AST: float(i)}) for i in range(0, 6)]

    result = waiver_candidates(snapshot, set(), Category.AST, cutoff=150, limit=2)

    assert [p.player_id for p in result] == [5, 4]
    assert 0 not in [p.player_id for p in result]


def test_waiver_candidates_empty_when_nobody_qualifies() -> None:
    assert waiver_candidates([], set(), Category.PTS, cutoff=150, limit=5) == []

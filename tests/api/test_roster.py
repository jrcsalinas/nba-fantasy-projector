import json
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nba_projector.adapters.repository import Repository
from nba_projector.api.dependencies import get_repository, get_roster, get_today
from nba_projector.config import get_settings
from nba_projector.domain.models import SORT_KEYS, Category, RankedPlayer, Window
from nba_projector.domain.roster import Roster, RosterPlayer
from nba_projector.main import app

DAY = date(2026, 10, 22)

ROSTER = Roster(
    active=(RosterPlayer(1, "Alpha"), RosterPlayer(2, "Bravo"), RosterPlayer(4, "Delta")),
    il=(RosterPlayer(3, "Charlie"),),
)


def make_player(
    player_id: int, overall: int, z: dict[Category, float] | None = None, **ranks: int
) -> RankedPlayer:
    values = {cat: 1.0 for cat in Category}
    values.update(z or {})
    return RankedPlayer(
        player_id=player_id,
        player_name=f"Player {player_id}",
        z=values,
        total=sum(values.values()),
        ranks={key: ranks.get(key, overall) for key in SORT_KEYS},
    )


WEAK = {Category.BLK: -2.0, Category.STL: -1.0, Category.AST: -0.5}


def snapshot() -> list[RankedPlayer]:
    return [
        make_player(1, 5, WEAK),
        make_player(2, 20, WEAK, pts=1),
        # IL player: huge negative PTS must not affect team totals.
        make_player(3, 30, {Category.PTS: -10.0}),
        # Candidates outside the roster.
        make_player(10, 200, {Category.BLK: 3.0, Category.STL: 0.2}),
        make_player(11, 160, {Category.BLK: 1.0, Category.STL: 2.5}),
        make_player(12, 100, {Category.BLK: 9.0}),  # inside the cutoff: likely rostered
    ]


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    return Repository(tmp_path / "test.db")


@pytest.fixture
def client(repo: Repository) -> Iterator[TestClient]:
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_today] = lambda: DAY
    app.dependency_overrides[get_roster] = lambda: ROSTER
    yield TestClient(app)
    app.dependency_overrides.clear()


def save(
    repo: Repository, day: date, players: list[RankedPlayer], window: Window = Window.SEASON
) -> None:
    repo.replace_snapshots(day, {window: players})


def test_roster_returns_active_then_il_with_team_totals(
    client: TestClient, repo: Repository
) -> None:
    save(repo, DAY, snapshot())

    body = client.get("/api/v1/roster").json()

    assert body["as_of"] == "2026-10-22"
    assert [(r["player_id"], r["slot"]) for r in body["rows"]] == [
        (1, "active"),
        (2, "active"),
        (4, "active"),
        (3, "il"),
    ]
    # Totals cover active ranked players only (players 1 and 2), not IL or unranked Delta.
    assert body["team"]["pts"] == 2.0
    assert body["team"]["blk"] == -4.0
    assert body["weak_categories"] == ["blk", "stl", "ast"]


def test_roster_player_missing_from_snapshot_is_listed_unranked(
    client: TestClient, repo: Repository
) -> None:
    save(repo, DAY, snapshot())

    rows = {r["player_id"]: r for r in client.get("/api/v1/roster").json()["rows"]}

    assert rows[4]["ranked"] is False
    assert rows[4]["rank"] is None
    assert rows[4]["z"] is None
    assert rows[4]["total"] is None
    assert rows[4]["player_name"] == "Delta"
    assert rows[1]["ranked"] is True
    assert rows[1]["rank"] == 5


def test_roster_sort_uses_category_rank_within_slot(client: TestClient, repo: Repository) -> None:
    save(repo, DAY, snapshot())

    rows = client.get("/api/v1/roster", params={"sort": "pts"}).json()["rows"]

    assert [r["player_id"] for r in rows if r["slot"] == "active"] == [2, 1, 4]
    assert rows[0]["rank"] == 1


def test_roster_trend_compares_with_earlier_snapshot(client: TestClient, repo: Repository) -> None:
    save(repo, DAY - timedelta(days=1), [make_player(1, 9, WEAK)])
    save(repo, DAY, snapshot())

    rows = {r["player_id"]: r for r in client.get("/api/v1/roster").json()["rows"]}

    assert rows[1]["trend"] == 4
    assert rows[1]["is_new"] is False
    assert rows[2]["trend"] is None
    assert rows[2]["is_new"] is True


def test_roster_window_without_players_lists_everyone_unranked(
    client: TestClient, repo: Repository
) -> None:
    repo.replace_snapshots(DAY, {Window.SEASON: snapshot(), Window.LAST_15: []})

    body = client.get("/api/v1/roster", params={"window": "l15"}).json()

    assert len(body["rows"]) == 4
    assert not any(r["ranked"] for r in body["rows"])
    assert body["team"] == {}
    assert body["weak_categories"] == []


def test_roster_no_snapshot_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/roster")

    assert response.status_code == 404
    assert response.json() == {"detail": "No rankings available yet"}


def test_roster_marks_old_snapshot_stale(client: TestClient, repo: Repository) -> None:
    save(repo, DAY - timedelta(days=3), snapshot())

    assert client.get("/api/v1/roster").json()["is_stale"] is True


def test_roster_invalid_window_or_sort_returns_422(client: TestClient) -> None:
    assert client.get("/api/v1/roster", params={"window": "l7"}).status_code == 422
    assert client.get("/api/v1/roster", params={"sort": "dunks"}).status_code == 422


def test_waivers_suggests_best_available_in_weakest_categories(
    client: TestClient, repo: Repository
) -> None:
    save(repo, DAY, snapshot())

    body = client.get("/api/v1/roster/waivers").json()

    assert body["cutoff"] == 150
    assert "rank cutoff" in body["availability_note"]
    assert [g["category"] for g in body["suggestions"]] == ["blk", "stl", "ast"]
    blk, stl, _ = body["suggestions"]
    assert blk["team_total"] == -4.0
    # Player 12 is inside the cutoff, so only 10 and 11 qualify; ordered by that category.
    assert [c["player_id"] for c in blk["candidates"]] == [10, 11]
    assert [c["player_id"] for c in stl["candidates"]] == [11, 10]
    assert blk["candidates"][0]["category_z"] == 3.0
    assert blk["candidates"][0]["overall_rank"] == 200


def test_waivers_cutoff_and_limit_params_apply(client: TestClient, repo: Repository) -> None:
    save(repo, DAY, snapshot())

    body = client.get("/api/v1/roster/waivers", params={"cutoff": 50, "limit": 1}).json()

    blk = body["suggestions"][0]
    assert body["cutoff"] == 50
    assert [c["player_id"] for c in blk["candidates"]] == [12]


def test_waivers_empty_window_returns_no_suggestions(client: TestClient, repo: Repository) -> None:
    repo.replace_snapshots(DAY, {Window.SEASON: snapshot(), Window.LAST_15: []})

    response = client.get("/api/v1/roster/waivers", params={"window": "l15"})

    assert response.status_code == 200
    assert response.json()["suggestions"] == []


@pytest.mark.parametrize("params", [{"cutoff": 0}, {"cutoff": 501}, {"limit": 0}, {"limit": 26}])
def test_waivers_invalid_params_return_422(client: TestClient, params: dict[str, int]) -> None:
    assert client.get("/api/v1/roster/waivers", params=params).status_code == 422


def test_waivers_no_snapshot_returns_404(client: TestClient) -> None:
    assert client.get("/api/v1/roster/waivers").status_code == 404


@pytest.fixture
def roster_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "roster.json"
    monkeypatch.setenv("NBA_PROJECTOR_ROSTER_PATH", str(path))
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def test_roster_file_missing_returns_404_with_hint(repo: Repository, roster_env: Path) -> None:
    save(repo, DAY, snapshot())
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_today] = lambda: DAY
    try:
        response = TestClient(app).get("/api/v1/roster")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": f"Add {roster_env.as_posix()}"}


def test_roster_file_invalid_returns_500_with_reason(repo: Repository, roster_env: Path) -> None:
    roster_env.write_text("{not json", encoding="utf-8")
    save(repo, DAY, snapshot())
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_today] = lambda: DAY
    try:
        response = TestClient(app).get("/api/v1/roster")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert "not valid JSON" in response.json()["detail"]


def test_roster_file_is_read_from_disk_when_not_overridden(
    repo: Repository, roster_env: Path
) -> None:
    roster_env.write_text(
        json.dumps({"active": [{"player_id": 1, "name": "Alpha"}]}), encoding="utf-8"
    )
    save(repo, DAY, snapshot())
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_today] = lambda: DAY
    try:
        rows = TestClient(app).get("/api/v1/roster").json()["rows"]
    finally:
        app.dependency_overrides.clear()

    assert [r["player_id"] for r in rows] == [1]

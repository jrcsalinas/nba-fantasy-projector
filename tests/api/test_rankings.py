from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nba_projector.adapters.repository import Repository
from nba_projector.api.routes import get_repository, get_today
from nba_projector.domain.models import SORT_KEYS, Category, RankedPlayer, Window
from nba_projector.main import app

DAY = date(2026, 10, 22)


def make_player(player_id: int, overall: int, **ranks: int) -> RankedPlayer:
    return RankedPlayer(
        player_id=player_id,
        player_name=f"Player {player_id}",
        z={cat: 0.5 for cat in Category},
        total=4.5,
        ranks={key: ranks.get(key, overall) for key in SORT_KEYS},
    )


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    return Repository(tmp_path / "test.db")


@pytest.fixture
def client(repo: Repository) -> Iterator[TestClient]:
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_today] = lambda: DAY
    yield TestClient(app)
    app.dependency_overrides.clear()


def save(repo: Repository, day: date, players: list[RankedPlayer], window=Window.SEASON) -> None:
    repo.replace_snapshots(day, {window: players})


def test_rankings_returns_rows_ordered_by_overall_rank_with_as_of(
    client: TestClient, repo: Repository
) -> None:
    save(repo, DAY, [make_player(1, 2), make_player(2, 1), make_player(3, 3)])

    body = client.get("/api/v1/rankings").json()

    assert body["window"] == "season"
    assert body["sort"] == "overall"
    assert body["as_of"] == "2026-10-22"
    assert [r["player_id"] for r in body["rows"]] == [2, 1, 3]
    assert [r["rank"] for r in body["rows"]] == [1, 2, 3]
    assert set(body["rows"][0]["z"]) == {c.value for c in Category}


def test_rankings_sort_by_category_uses_category_rank_and_trend(
    client: TestClient, repo: Repository
) -> None:
    save(repo, DAY - timedelta(days=1), [make_player(1, 1, pts=3), make_player(2, 2, pts=1)])
    save(repo, DAY, [make_player(1, 1, pts=2), make_player(2, 2, pts=1)])

    rows = client.get("/api/v1/rankings", params={"sort": "pts"}).json()["rows"]

    assert [(r["player_id"], r["rank"], r["trend"]) for r in rows] == [(2, 1, 0), (1, 2, 1)]


def test_rankings_trend_compares_with_most_recent_earlier_snapshot(
    client: TestClient, repo: Repository
) -> None:
    save(repo, DAY - timedelta(days=2), [make_player(1, 4)])
    save(repo, DAY, [make_player(1, 1)])

    rows = client.get("/api/v1/rankings").json()["rows"]

    assert rows[0]["trend"] == 3
    assert rows[0]["is_new"] is False


def test_rankings_single_snapshot_has_null_trend_and_not_new(
    client: TestClient, repo: Repository
) -> None:
    save(repo, DAY, [make_player(1, 1)])

    row = client.get("/api/v1/rankings").json()["rows"][0]

    assert row["trend"] is None
    assert row["is_new"] is False


def test_rankings_player_missing_from_previous_is_new(client: TestClient, repo: Repository) -> None:
    save(repo, DAY - timedelta(days=1), [make_player(1, 1)])
    save(repo, DAY, [make_player(1, 1), make_player(2, 2)])

    rows = {r["player_id"]: r for r in client.get("/api/v1/rankings").json()["rows"]}

    assert rows[2]["trend"] is None
    assert rows[2]["is_new"] is True
    assert rows[1]["is_new"] is False


def test_rankings_window_param_selects_window_snapshot(
    client: TestClient, repo: Repository
) -> None:
    save(repo, DAY, [make_player(1, 1)], Window.SEASON)
    save(repo, DAY, [make_player(7, 1)], Window.LAST_15)

    body = client.get("/api/v1/rankings", params={"window": "l15"}).json()

    assert body["window"] == "l15"
    assert [r["player_id"] for r in body["rows"]] == [7]


def test_rankings_empty_snapshot_returns_200_with_empty_rows(tmp_path: Path) -> None:
    class EmptySnapshotRepository(Repository):
        """A snapshot date that exists but has no eligible players."""

        def snapshot_dates(self, window: Window, limit: int = 2) -> list[date]:
            return [DAY]

    stub = EmptySnapshotRepository(tmp_path / "stub.db")
    app.dependency_overrides[get_repository] = lambda: stub
    app.dependency_overrides[get_today] = lambda: DAY
    try:
        response = TestClient(app).get("/api/v1/rankings")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["rows"] == []


def test_rankings_no_snapshot_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/rankings")

    assert response.status_code == 404
    assert response.json() == {"detail": "No rankings available yet"}


@pytest.mark.parametrize(
    "params",
    [
        {"window": "l7"},
        {"sort": "dunks"},
        {"limit": 0},
        {"limit": 501},
    ],
)
def test_rankings_invalid_params_return_422(client: TestClient, params: dict[str, object]) -> None:
    assert client.get("/api/v1/rankings", params=params).status_code == 422


def test_rankings_limit_caps_rows(client: TestClient, repo: Repository) -> None:
    save(repo, DAY, [make_player(i, i) for i in range(1, 6)])

    rows = client.get("/api/v1/rankings", params={"limit": 2}).json()["rows"]

    assert [r["player_id"] for r in rows] == [1, 2]


def test_rankings_marks_old_snapshot_stale(client: TestClient, repo: Repository) -> None:
    save(repo, DAY, [make_player(1, 1)])

    assert client.get("/api/v1/rankings").json()["is_stale"] is False

    app.dependency_overrides[get_today] = lambda: DAY + timedelta(days=1)
    assert client.get("/api/v1/rankings").json()["is_stale"] is False

    app.dependency_overrides[get_today] = lambda: DAY + timedelta(days=2)
    assert client.get("/api/v1/rankings").json()["is_stale"] is True

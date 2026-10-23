import copy
import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from nba_projector.adapters.nba_api_provider import (
    ProviderSchemaError,
    call_with_retry,
    parse_game_logs,
    parse_schedule,
)
from nba_projector.domain.models import GameLog

FIXTURES = Path(__file__).parent.parent / "fixtures"
SEASON = "2026-27"


@pytest.fixture
def game_logs_payload() -> dict[str, Any]:
    return json.loads((FIXTURES / "game_logs.json").read_text())


@pytest.fixture
def schedule_payload() -> dict[str, Any]:
    return json.loads((FIXTURES / "schedule.json").read_text())


def test_parse_game_logs_maps_fields_to_gamelog(game_logs_payload: dict[str, Any]) -> None:
    logs = parse_game_logs(game_logs_payload, SEASON)

    assert len(logs) == 19
    assert logs[0] == GameLog(
        season="2026-27",
        game_id="0022600001",
        game_date=date(2026, 10, 20),
        player_id=1642450,
        player_name="Daniss Jenkins",
        minutes=15.0,
        pts=3,
        reb=2,
        ast=3,
        stl=0,
        blk=0,
        fg3m=1,
        fgm=1,
        fga=3,
        ftm=0,
        fta=0,
        tov=1,
    )


def test_parse_game_logs_keeps_traded_player_as_separate_games(
    game_logs_payload: dict[str, Any],
) -> None:
    logs = parse_game_logs(game_logs_payload, SEASON)

    player_games = [log.game_id for log in logs if log.player_id == 1642450]

    assert len(player_games) == 2
    assert len(set(player_games)) == 2


def test_parse_game_logs_skips_rows_with_null_minutes_or_stats(
    game_logs_payload: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    result_set = game_logs_payload["resultSets"][0]
    headers = result_set["headers"]
    result_set["rowSet"][1][headers.index("MIN")] = None
    result_set["rowSet"][2][headers.index("PTS")] = None

    with caplog.at_level(logging.WARNING):
        logs = parse_game_logs(game_logs_payload, SEASON)

    assert len(logs) == 17
    assert "Skipped 2" in caplog.text


def test_parse_game_logs_raises_on_missing_expected_columns(
    game_logs_payload: dict[str, Any],
) -> None:
    result_set = game_logs_payload["resultSets"][0]
    index = result_set["headers"].index("TOV")
    del result_set["headers"][index]
    for row in result_set["rowSet"]:
        del row[index]

    with pytest.raises(ProviderSchemaError, match="TOV"):
        parse_game_logs(game_logs_payload, SEASON)


def test_parse_schedule_returns_aware_utc_start_and_final_flag(
    schedule_payload: dict[str, Any],
) -> None:
    played = parse_schedule(schedule_payload, date(2026, 10, 22))
    upcoming = parse_schedule(schedule_payload, date(2026, 10, 23))

    assert [g.game_id for g in played] == ["0022600006", "0022600007"]
    assert played[0].start_utc == datetime(2026, 10, 22, 23, 0, tzinfo=UTC)
    assert played[0].start_utc.tzinfo is not None
    assert all(g.is_final for g in played)
    assert len(upcoming) == 2
    assert not any(g.is_final for g in upcoming)


def test_parse_schedule_excludes_preseason_and_other_dates(
    schedule_payload: dict[str, Any],
) -> None:
    assert parse_schedule(schedule_payload, date(2026, 10, 3)) == []
    assert parse_schedule(copy.deepcopy(schedule_payload), date(2026, 1, 1)) == []


def test_call_with_retry_succeeds_after_transient_failures() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("transient")
        return "ok"

    result = call_with_retry(flaky, max_retries=3, base_delay=0.5, sleep=sleeps.append)

    assert result == "ok"
    assert sleeps == [0.5, 1.0]


def test_call_with_retry_raises_after_max_retries() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def always_fails() -> None:
        calls.append(1)
        raise TimeoutError(f"attempt {len(calls)}")

    with pytest.raises(TimeoutError, match="attempt 3"):
        call_with_retry(always_fails, max_retries=2, base_delay=1.0, sleep=sleeps.append)

    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]

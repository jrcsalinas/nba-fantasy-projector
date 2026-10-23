from pathlib import Path

import pytest

from nba_projector.adapters.yahoo import YahooAuthError
from nba_projector.config import Settings
from nba_projector.jobs.yahoo import _parse_args, client_from_settings, main


def make_settings(tmp_path: Path, **overrides: str) -> Settings:
    values = {
        "yahoo_client_id": "id",
        "yahoo_client_secret": "secret",
        "yahoo_redirect_uri": "https://localhost:8000",
        "yahoo_token_path": str(tmp_path / "token.json"),
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_client_from_settings_names_every_missing_setting(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, yahoo_client_id="", yahoo_client_secret="")

    with pytest.raises(YahooAuthError) as info:
        client_from_settings(settings)

    assert "NBA_PROJECTOR_YAHOO_CLIENT_ID" in str(info.value)
    assert "NBA_PROJECTOR_YAHOO_CLIENT_SECRET" in str(info.value)
    assert "REDIRECT_URI" not in str(info.value)


def test_client_from_settings_never_prints_the_secret(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, yahoo_client_secret="s3cr3t-value-123")

    assert "s3cr3t-value-123" not in repr(settings)
    assert "s3cr3t-value-123" not in str(settings)


def test_login_without_code_prints_the_sign_in_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("nba_projector.jobs.yahoo.get_settings", lambda: make_settings(tmp_path))

    code = main(["login", "--no-browser"])

    out = capsys.readouterr().out
    assert code == 0
    assert "https://api.login.yahoo.com/oauth2/request_auth?" in out
    assert "client_id=id" in out
    assert "--code" in out


def test_commands_report_missing_settings_instead_of_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "nba_projector.jobs.yahoo.get_settings", lambda: make_settings(tmp_path, yahoo_client_id="")
    )

    assert main(["check"]) == 1
    assert "CLIENT_ID" in capsys.readouterr().out


def test_check_without_signing_in_says_how_to_sign_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "nba_projector.jobs.yahoo.get_settings",
        lambda: make_settings(tmp_path, yahoo_league_id="1", yahoo_team_id="2"),
    )

    assert main(["check"]) == 1
    assert "login" in capsys.readouterr().out


def test_cli_requires_a_command() -> None:
    with pytest.raises(SystemExit):
        _parse_args([])
    assert _parse_args(["login", "--code", "abc"]).code == "abc"

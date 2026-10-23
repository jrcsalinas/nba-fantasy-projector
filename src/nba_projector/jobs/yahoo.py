"""Yahoo league tools: sign in once, then check the connection.

Run with ``uv run python -m nba_projector.jobs.yahoo`` and one of:

- ``login``: print the Yahoo sign-in address (and open it in your browser).
- ``login --code "<address or code>"``: finish signing in with what Yahoo sent back.
- ``check``: ask Yahoo for your league, roster, and a few taken players.
"""

import argparse
import json
import logging
import webbrowser
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from nba_projector.adapters.yahoo import (
    YahooAuthError,
    YahooClient,
    YahooError,
    build_authorization_url,
    extract_code,
    find_all,
)
from nba_projector.config import Settings, get_settings

logger = logging.getLogger(__name__)

_SAMPLE_SIZE = 5


def client_from_settings(settings: Settings) -> YahooClient:
    """Build a client from settings.

    Raises:
        YahooAuthError: Naming every Yahoo setting that is still empty.
    """
    required = {
        "NBA_PROJECTOR_YAHOO_CLIENT_ID": settings.yahoo_client_id,
        "NBA_PROJECTOR_YAHOO_CLIENT_SECRET": settings.yahoo_client_secret.get_secret_value(),
        "NBA_PROJECTOR_YAHOO_REDIRECT_URI": settings.yahoo_redirect_uri,
    }
    missing = [name for name, value in required.items() if not value.strip()]
    if missing:
        raise YahooAuthError(f"Set these in your .env file first: {', '.join(missing)}")
    return YahooClient(
        client_id=settings.yahoo_client_id.strip(),
        client_secret=settings.yahoo_client_secret.get_secret_value().strip(),
        redirect_uri=settings.yahoo_redirect_uri.strip(),
        token_path=settings.yahoo_token_path,
    )


def _login(settings: Settings, code: str | None, open_browser: bool) -> None:
    client = client_from_settings(settings)
    if code is None:
        url = build_authorization_url(
            settings.yahoo_client_id.strip(), settings.yahoo_redirect_uri.strip()
        )
        print("1. Open this address and approve access (it may open automatically):\n")
        print(url)
        print(
            "\n2. Yahoo then sends your browser to your redirect address. The page will probably\n"
            "   fail to load; that is expected. Copy the WHOLE address from the address bar.\n"
            "\n3. Finish signing in:\n"
            '   uv run python -m nba_projector.jobs.yahoo login --code "<paste the address>"'
        )
        if open_browser:
            webbrowser.open(url)
        return
    client.exchange_code(extract_code(code))
    print("Signed in. Next: uv run python -m nba_projector.jobs.yahoo check")


def _save_raw(raw_dir: Path, name: str, payload: dict[str, Any]) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"yahoo-{name}.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")


def _check(settings: Settings) -> bool:
    """Run each Yahoo call in turn, printing what worked. Returns True if all succeeded."""
    client = client_from_settings(settings)
    league_id, team_id = settings.yahoo_league_id.strip(), settings.yahoo_team_id.strip()
    if not league_id or not team_id:
        raise YahooAuthError(
            "Set NBA_PROJECTOR_YAHOO_LEAGUE_ID and NBA_PROJECTOR_YAHOO_TEAM_ID in your .env file."
        )
    ok = True

    def step(label: str, name: str, path: str) -> dict[str, Any] | None:
        nonlocal ok
        try:
            payload = client.get(path)
        except YahooError as exc:
            ok = False
            print(f"FAILED  {label}: {exc}")
            return None
        _save_raw(settings.raw_dir, name, payload)
        return payload

    game = step("NBA game", "game", "game/nba")
    if game is None:
        return False
    game_key = str(find_all(game, "game_key")[0])
    seasons = find_all(game, "season")
    print(f"OK      NBA game key {game_key}, season {seasons[0] if seasons else '?'}")

    league_key = f"{game_key}.l.{league_id}"
    league = step("League", "league", f"league/{league_key}")
    if league is not None:
        names, teams = find_all(league, "name"), find_all(league, "num_teams")
        print(f"OK      League '{names[0] if names else '?'}' ({teams[0] if teams else '?'} teams)")

    roster = step("My roster", "roster", f"team/{league_key}.t.{team_id}/roster")
    if roster is not None:
        players = find_all(roster, "full")
        print(f"OK      My roster: {len(players)} players: {', '.join(players)}")

    taken = step(
        "Taken players", "taken", f"league/{league_key}/players;status=T;count={_SAMPLE_SIZE}"
    )
    if taken is not None:
        print(f"OK      Taken players (first {_SAMPLE_SIZE}): {', '.join(find_all(taken, 'full'))}")

    if ok:
        print(f"\nAll good. Raw responses were saved in {settings.raw_dir} as yahoo-*.json")
    return ok


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Yahoo league tools.")
    sub = parser.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login", help="Sign in to Yahoo (two steps)")
    login.add_argument("--code", help="The address (or code) Yahoo sent your browser back to")
    login.add_argument("--no-browser", action="store_true", help="Only print the sign-in address")
    sub.add_parser("check", help="Test the connection to your league")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns a non-zero exit code on failure."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    settings = get_settings()
    try:
        if args.command == "login":
            _login(settings, args.code, open_browser=not args.no_browser)
            return 0
        return 0 if _check(settings) else 1
    except YahooError as exc:
        print(f"ERROR   {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

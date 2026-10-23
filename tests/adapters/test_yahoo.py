import json
from pathlib import Path
from typing import Any

import pytest

from nba_projector.adapters.yahoo import (
    Tokens,
    YahooAuthError,
    YahooClient,
    YahooError,
    build_authorization_url,
    extract_code,
    find_all,
    load_tokens,
    save_tokens,
)

REDIRECT = "https://localhost:8000"
API = "https://fantasysports.yahooapis.com/fantasy/v2"


class FakeResponse:
    def __init__(self, status: int = 200, payload: Any = None, text: str = "") -> None:
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text or json.dumps(self._payload)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        return self._payload


class FakeSession:
    def __init__(
        self, posts: list[FakeResponse] | None = None, gets: list[FakeResponse] | None = None
    ) -> None:
        self.posts = list(posts or [])
        self.gets = list(gets or [])
        self.post_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.post_calls.append({"url": url, **kwargs})
        return self.posts.pop(0)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.get_calls.append({"url": url, **kwargs})
        return self.gets.pop(0)


def make_client(tmp_path: Path, session: FakeSession, now: float = 1000.0) -> YahooClient:
    return YahooClient(
        client_id="my-id",
        client_secret="my-secret",
        redirect_uri=REDIRECT,
        token_path=tmp_path / "token.json",
        session=session,  # type: ignore[arg-type]
        now=lambda: now,
    )


def token_response(
    access: str = "A1", refresh: str | None = "R1", expires: int = 3600
) -> FakeResponse:
    payload: dict[str, Any] = {
        "access_token": access,
        "expires_in": expires,
        "token_type": "bearer",
    }
    if refresh is not None:
        payload["refresh_token"] = refresh
    return FakeResponse(200, payload)


def test_authorization_url_has_required_parameters() -> None:
    url = build_authorization_url("my-id", REDIRECT)

    assert url.startswith("https://api.login.yahoo.com/oauth2/request_auth?")
    assert "client_id=my-id" in url
    assert "redirect_uri=https%3A%2F%2Flocalhost%3A8000" in url
    assert "response_type=code" in url


def test_extract_code_accepts_full_redirect_address_or_bare_code() -> None:
    assert extract_code("https://localhost:8000/?code=abc123&state=x") == "abc123"
    assert extract_code("  abc123  ") == "abc123"


def test_extract_code_rejects_address_without_code() -> None:
    with pytest.raises(YahooAuthError, match="code="):
        extract_code("https://localhost:8000/?error=access_denied")


def test_tokens_round_trip_through_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "token.json"

    save_tokens(path, Tokens("A", "R", 123.0))

    assert load_tokens(path) == Tokens("A", "R", 123.0)
    assert load_tokens(tmp_path / "missing.json") is None


def test_exchange_code_posts_basic_auth_and_saves_tokens(tmp_path: Path) -> None:
    session = FakeSession(posts=[token_response("A1", "R1")])
    client = make_client(tmp_path, session)

    client.exchange_code("the-code")

    call = session.post_calls[0]
    assert call["url"] == "https://api.login.yahoo.com/oauth2/get_token"
    assert call["data"] == {
        "grant_type": "authorization_code",
        "code": "the-code",
        "redirect_uri": REDIRECT,
    }
    assert call["headers"]["Authorization"].startswith("Basic ")
    assert "my-secret" not in call["headers"]["Authorization"]  # base64, not plain text
    assert load_tokens(tmp_path / "token.json") == Tokens("A1", "R1", 1000.0 + 3600)


def test_exchange_code_failure_raises_a_clear_error(tmp_path: Path) -> None:
    session = FakeSession(posts=[FakeResponse(400, text='{"error":"invalid_grant"}')])
    client = make_client(tmp_path, session)

    with pytest.raises(YahooAuthError, match="400") as info:
        client.exchange_code("bad")

    assert "my-secret" not in str(info.value)


def test_get_without_saved_tokens_tells_user_to_sign_in(tmp_path: Path) -> None:
    client = make_client(tmp_path, FakeSession())

    with pytest.raises(YahooAuthError, match="login"):
        client.get("game/nba")


def test_get_sends_bearer_token_and_json_format(tmp_path: Path) -> None:
    save_tokens(tmp_path / "token.json", Tokens("A1", "R1", 5000.0))
    session = FakeSession(gets=[FakeResponse(200, {"fantasy_content": {"ok": 1}})])
    client = make_client(tmp_path, session)

    result = client.get("league/466.l.31510;out=settings")

    assert result == {"fantasy_content": {"ok": 1}}
    call = session.get_calls[0]
    assert call["url"] == f"{API}/league/466.l.31510;out=settings"
    assert call["headers"]["Authorization"] == "Bearer A1"
    assert call["params"] == {"format": "json"}


def test_get_refreshes_an_expired_token_first_and_keeps_old_refresh_token(
    tmp_path: Path,
) -> None:
    save_tokens(tmp_path / "token.json", Tokens("OLD", "R1", 1030.0))  # inside the expiry margin
    session = FakeSession(posts=[token_response("NEW", refresh=None)], gets=[FakeResponse(200, {})])
    client = make_client(tmp_path, session, now=1000.0)

    client.get("game/nba")

    assert session.post_calls[0]["data"]["grant_type"] == "refresh_token"
    assert session.post_calls[0]["data"]["refresh_token"] == "R1"
    assert session.get_calls[0]["headers"]["Authorization"] == "Bearer NEW"
    assert load_tokens(tmp_path / "token.json") == Tokens("NEW", "R1", 1000.0 + 3600)


def test_get_stores_a_rotated_refresh_token(tmp_path: Path) -> None:
    save_tokens(tmp_path / "token.json", Tokens("OLD", "R1", 0.0))
    session = FakeSession(posts=[token_response("NEW", refresh="R2")], gets=[FakeResponse(200, {})])
    client = make_client(tmp_path, session)

    client.get("game/nba")

    saved = load_tokens(tmp_path / "token.json")
    assert saved is not None
    assert saved.refresh_token == "R2"


def test_get_retries_once_after_401_with_a_fresh_token(tmp_path: Path) -> None:
    save_tokens(tmp_path / "token.json", Tokens("A1", "R1", 99999.0))
    session = FakeSession(
        posts=[token_response("A2", "R1")],
        gets=[FakeResponse(401, text="expired"), FakeResponse(200, {"ok": True})],
    )
    client = make_client(tmp_path, session)

    assert client.get("game/nba") == {"ok": True}
    assert session.get_calls[1]["headers"]["Authorization"] == "Bearer A2"


def test_get_raises_a_clear_error_on_api_failure(tmp_path: Path) -> None:
    save_tokens(tmp_path / "token.json", Tokens("A1", "R1", 99999.0))
    session = FakeSession(gets=[FakeResponse(403, text="not allowed")])
    client = make_client(tmp_path, session)

    with pytest.raises(YahooError, match="403"):
        client.get("league/1.l.2")


def test_find_all_walks_nested_lists_and_dicts() -> None:
    node = {"a": [{"name": {"full": "X"}}, [{"x": 1}, {"name": {"full": "Y"}}]], "full": "Z"}

    assert find_all(node, "full") == ["X", "Y", "Z"]

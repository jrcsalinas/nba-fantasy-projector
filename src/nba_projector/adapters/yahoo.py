"""Yahoo Fantasy Sports API client: OAuth 2.0 sign-in and authenticated GET requests.

Flow (https://developer.yahoo.com/oauth2/guide/flows_authcode/): send the user to
``AUTH_URL``, they approve and Yahoo redirects to the registered redirect URI with ``?code=``.
That code is exchanged at ``TOKEN_URL`` (HTTP Basic auth with the client id and secret) for an
access token (about an hour) and a refresh token, which is used to get new access tokens.
"""

import base64
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

logger = logging.getLogger(__name__)

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
API_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"

_EXPIRY_MARGIN_SECONDS = 60
_ERROR_BODY_CHARS = 300


class YahooError(Exception):
    """A Yahoo API call failed."""


class YahooAuthError(YahooError):
    """Signing in to Yahoo failed or has not been done yet."""


@dataclass(frozen=True)
class Tokens:
    """OAuth tokens; ``expires_at`` is the access token's expiry as a Unix timestamp."""

    access_token: str
    refresh_token: str
    expires_at: float


def build_authorization_url(client_id: str, redirect_uri: str) -> str:
    """Return the Yahoo page where the user approves access."""
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "language": "en-us",
        }
    )
    return f"{AUTH_URL}?{query}"


def extract_code(text: str) -> str:
    """Get the authorization code from a pasted redirect address, or accept a bare code.

    Raises:
        YahooAuthError: If an address was pasted that has no ``code=`` in it.
    """
    text = text.strip()
    if text.lower().startswith("http"):
        codes = parse_qs(urlparse(text).query).get("code")
        if not codes:
            raise YahooAuthError("That address has no code= in it; copy the whole address.")
        return codes[0]
    return text


def load_tokens(path: Path) -> Tokens | None:
    """Read saved tokens, or ``None`` if the file does not exist."""
    try:
        return Tokens(**json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return None


def save_tokens(path: Path, tokens: Tokens) -> None:
    """Write tokens to ``path``, readable only by the current user where the OS supports it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(tokens)), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        logger.debug("Could not restrict permissions on %s", path)


def find_all(node: Any, key: str) -> list[Any]:
    """Collect every value stored under ``key`` anywhere in nested dicts and lists.

    Yahoo's JSON nests data as lists of single-key dicts, so a recursive search is the simplest
    way to pull a field out without depending on the exact nesting.
    """
    found: list[Any] = []
    if isinstance(node, dict):
        for k, value in node.items():
            if k == key:
                found.append(value)
            else:
                found.extend(find_all(value, key))
    elif isinstance(node, list):
        for item in node:
            found.extend(find_all(item, key))
    return found


class YahooClient:
    """Signs in with OAuth 2.0 and makes read-only Fantasy Sports API calls."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        token_path: Path,
        *,
        session: requests.Session | None = None,
        now: Callable[[], float] = time.time,
        timeout: float = 30.0,
    ) -> None:
        """Configure the client.

        Args:
            client_id: Yahoo app Client ID (consumer key).
            client_secret: Yahoo app Client Secret.
            redirect_uri: The redirect URI registered on the app, exactly.
            token_path: Where the tokens are stored between runs.
            session: HTTP session, injectable for tests.
            now: Clock returning Unix time, injectable for tests.
            timeout: Seconds before a request times out.
        """
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._token_path = token_path
        self._session = session or requests.Session()
        self._now = now
        self._timeout = timeout

    def exchange_code(self, code: str) -> None:
        """Trade an authorization code for tokens and save them.

        Raises:
            YahooAuthError: If Yahoo rejects the code.
        """
        self._request_tokens(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri,
            },
            previous_refresh_token=None,
        )

    def get(self, path: str, **params: str) -> dict[str, Any]:
        """GET a Fantasy Sports API path and return the parsed JSON.

        A 401 triggers one token refresh and retry.

        Args:
            path: Path under the API base, e.g. ``league/466.l.31510/teams``.
            **params: Extra query parameters; ``format=json`` is always added.

        Raises:
            YahooAuthError: If not signed in.
            YahooError: If Yahoo returns an error.
        """
        response = self._get(path, params, self._access_token())
        if response.status_code == 401:
            logger.info("Yahoo returned 401; refreshing the access token and retrying once")
            response = self._get(path, params, self._access_token(force_refresh=True))
        if not response.ok:
            body = response.text[:_ERROR_BODY_CHARS]
            raise YahooError(f"Yahoo API error {response.status_code} for {path}: {body}")
        return response.json()

    def _get(self, path: str, params: dict[str, str], access_token: str) -> requests.Response:
        return self._session.get(
            f"{API_BASE}/{path.lstrip('/')}",
            params={**params, "format": "json"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=self._timeout,
        )

    def _access_token(self, *, force_refresh: bool = False) -> str:
        tokens = load_tokens(self._token_path)
        if tokens is None:
            raise YahooAuthError(
                "Not signed in to Yahoo. Run: uv run python -m nba_projector.jobs.yahoo login"
            )
        expiring = tokens.expires_at - self._now() <= _EXPIRY_MARGIN_SECONDS
        if force_refresh or expiring:
            tokens = self._request_tokens(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": tokens.refresh_token,
                    "redirect_uri": self._redirect_uri,
                },
                previous_refresh_token=tokens.refresh_token,
            )
        return tokens.access_token

    def _request_tokens(self, data: dict[str, str], previous_refresh_token: str | None) -> Tokens:
        credentials = f"{self._client_id}:{self._client_secret}".encode()
        response = self._session.post(
            TOKEN_URL,
            data=data,
            headers={
                "Authorization": f"Basic {base64.b64encode(credentials).decode()}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=self._timeout,
        )
        if not response.ok:
            body = response.text[:_ERROR_BODY_CHARS]
            raise YahooAuthError(f"Yahoo rejected the sign-in ({response.status_code}): {body}")
        payload = response.json()
        # Yahoo may rotate the refresh token; when it does not, keep the one we have.
        refresh_token = payload.get("refresh_token") or previous_refresh_token
        if not refresh_token or "access_token" not in payload:
            raise YahooAuthError("Yahoo's sign-in response had no access or refresh token.")
        tokens = Tokens(
            access_token=payload["access_token"],
            refresh_token=refresh_token,
            expires_at=self._now() + float(payload.get("expires_in", 3600)),
        )
        save_tokens(self._token_path, tokens)
        return tokens

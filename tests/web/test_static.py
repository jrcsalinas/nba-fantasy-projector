"""Tests for the vendored HTMX file and the static mount."""

import hashlib
from pathlib import Path

import pytest
from starlette.routing import Mount

import nbaproj.web

STATIC_DIR = Path(nbaproj.web.__file__).parent / "static"
HTMX_SHA256 = "d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717"
HTMX_SIZE = 52182


def test_htmx_is_the_verified_release():
    """The file is byte for byte the htmx 2.0.11 release checked against npm's hash."""
    script = (STATIC_DIR / "htmx.min.js").read_bytes()

    assert hashlib.sha256(script).hexdigest() == HTMX_SHA256
    assert len(script) == HTMX_SIZE
    licence = (STATIC_DIR / "LICENSE-htmx.txt").read_text(encoding="utf-8")
    assert licence.splitlines()[0] == "Zero-Clause BSD"
    assert sorted(path.name for path in STATIC_DIR.iterdir()) == [
        "LICENSE-htmx.txt",
        "htmx.min.js",
    ]


def test_static_files_are_served_without_a_login(client):
    """The library needs no login (it holds nothing private) and is served as JavaScript."""
    response = client.get("/static/htmx.min.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert response.content == (STATIC_DIR / "htmx.min.js").read_bytes()
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", ["/static/nope.js", "/static/..%2Fapp.py", "/static/", "/static"])
def test_static_mount_does_not_expose_anything_else(client, path):
    """Missing files, parent folders, and the folder itself are all just not found."""
    response = client.get(path, follow_redirects=False)

    assert response.status_code in (404, 307)
    assert b"class App" not in response.content and b"def create_app" not in response.content


def test_the_static_mount_is_the_only_extra_open_path(app):
    """Apart from the routers, the app mounts exactly one thing: /static."""
    mounts = [route.path for route in app.routes if isinstance(route, Mount)]

    assert mounts == ["/static"]

"""SPA catch-all route: path containment + API 404 fallthrough.

These live in their own module because the route under test only exists when a
production frontend build is present (`backend/static/`), which never happens in
a dev checkout or CI — that gap is exactly why an unauthenticated path-traversal
shipped. `mount_spa` takes the directory as an argument so the real route can be
exercised against a temp build here.
"""
import os
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from main import mount_spa, resolve_static_file


@pytest.fixture
def build(tmp_path):
    """A minimal 'production build' plus a secret sitting next to it."""
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>spa</html>")
    (static / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "wohub.db").write_text("SQLite format 3\x00SECRET")
    return static


@pytest.fixture
def spa_client(build):
    app = FastAPI()
    mount_spa(app, str(build))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---- resolve_static_file (the containment primitive) ----------------------

def test_resolves_real_file_inside_root(build):
    assert resolve_static_file(str(build), "index.html") == \
        os.path.realpath(build / "index.html")


@pytest.mark.parametrize("path", [
    "../data/wohub.db",
    "../../etc/passwd",
    "foo/../../data/wohub.db",
    "....//....//data/wohub.db",
    "/etc/passwd",
])
def test_rejects_escapes(build, path):
    assert resolve_static_file(str(build), path) is None


def test_rejects_directory_and_missing(build):
    assert resolve_static_file(str(build), "assets") is None
    assert resolve_static_file(str(build), "nope.js") is None


def test_rejects_symlink_pointing_outside(build, tmp_path):
    link = build / "leak.db"
    os.symlink(tmp_path / "data" / "wohub.db", link)
    assert resolve_static_file(str(build), "leak.db") is None


# ---- end-to-end through the ASGI stack (where %2e%2e%2f is decoded) -------

@pytest.mark.asyncio
async def test_serves_static_asset(spa_client):
    resp = await spa_client.get("/index.html")
    assert resp.status_code == 200
    assert "spa" in resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [
    "/%2e%2e%2fdata%2fwohub.db",
    "/..%2f..%2fetc%2fpasswd",
    "/../data/wohub.db",
])
async def test_traversal_never_leaks_file(spa_client, target):
    """An escape attempt must fall back to the SPA shell, never the target."""
    resp = await spa_client.get(target)
    assert "SECRET" not in resp.text
    assert "root:" not in resp.text
    assert resp.status_code == 200 and "spa" in resp.text


@pytest.mark.asyncio
async def test_unknown_route_falls_back_to_index(spa_client):
    resp = await spa_client.get("/trade")
    assert resp.status_code == 200 and "spa" in resp.text


@pytest.mark.asyncio
async def test_unmatched_api_path_404s_instead_of_html(spa_client):
    resp = await spa_client.get("/api/does-not-exist")
    assert resp.status_code == 404
    assert "spa" not in resp.text

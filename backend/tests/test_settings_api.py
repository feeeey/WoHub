import pytest


@pytest.mark.asyncio
async def test_get_info(client):
    resp = await client.get("/api/settings/info")
    assert resp.status_code == 200
    data = resp.json()
    assert "version" in data
    assert "cache_ttl" in data


@pytest.mark.asyncio
async def test_get_cookies_empty(client):
    resp = await client.get("/api/settings/cookies")
    assert resp.status_code == 200
    data = resp.json()
    assert "ok" in data


@pytest.mark.asyncio
async def test_update_cookies(client):
    resp = await client.put("/api/settings/cookies", json={
        "cookies": "sessionid=abc123; sessionid_sign=xyz789"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["count"] == 2

    # Verify it was saved
    get_resp = await client.get("/api/settings/cookies")
    assert get_resp.json()["has_cookies"] is True

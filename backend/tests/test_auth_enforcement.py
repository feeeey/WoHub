import pytest


@pytest.mark.no_auth_override
@pytest.mark.asyncio
async def test_protected_endpoint_rejects_without_cookie(client):
    resp = await client.get("/api/settings/info")
    assert resp.status_code == 401


@pytest.mark.no_auth_override
@pytest.mark.asyncio
async def test_protected_endpoint_allows_with_valid_cookie(client):
    login = await client.post("/api/auth/login", data={"password": "testpass"})
    session = login.cookies.get("session")
    resp = await client.get("/api/settings/info", cookies={"session": session})
    assert resp.status_code != 401


@pytest.mark.no_auth_override
@pytest.mark.asyncio
async def test_public_endpoints_allowed_without_cookie(client):
    assert (await client.get("/api/health")).status_code == 200
    status = await client.get("/api/auth/status")
    assert status.status_code == 200
    assert status.json()["authenticated"] is False

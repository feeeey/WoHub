import pytest


@pytest.mark.asyncio
async def test_login_success(client):
    response = await client.post(
        "/api/auth/login",
        data={"password": "testpass"},
    )
    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert "session" in response.cookies


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    response = await client.post(
        "/api/auth/login",
        data={"password": "wrong"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_auth_status_not_logged_in(client):
    response = await client.get("/api/auth/status")
    assert response.status_code == 200
    assert response.json()["authenticated"] is False


@pytest.mark.asyncio
async def test_auth_status_logged_in(client):
    login = await client.post(
        "/api/auth/login",
        data={"password": "testpass"},
    )
    session_cookie = login.cookies.get("session")

    response = await client.get(
        "/api/auth/status",
        cookies={"session": session_cookie},
    )
    assert response.status_code == 200
    assert response.json()["authenticated"] is True


@pytest.mark.asyncio
async def test_logout(client):
    login = await client.post(
        "/api/auth/login",
        data={"password": "testpass"},
    )
    session_cookie = login.cookies.get("session")

    response = await client.post(
        "/api/auth/logout",
        cookies={"session": session_cookie},
    )
    assert response.status_code == 200

    status = await client.get("/api/auth/status")
    assert status.json()["authenticated"] is False

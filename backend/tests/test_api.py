import pytest


@pytest.mark.asyncio
async def test_health_check(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


@pytest.mark.asyncio
async def test_health_check_includes_db_status(client):
    response = await client.get("/api/health")
    data = response.json()
    assert data["database"] == "connected"

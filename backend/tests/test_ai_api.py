import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_get_ai_config(client):
    resp = await client.get("/api/ai/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "api_key_display" in data
    assert "base_url" in data
    assert "model" in data


@pytest.mark.asyncio
async def test_update_ai_config(client):
    resp = await client.put("/api/ai/config", json={
        "api_key": "sk-test123",
        "base_url": "https://api.example.com/v1",
        "model": "gpt-4o-mini",
        "max_tokens": 500,
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    get_resp = await client.get("/api/ai/config")
    data = get_resp.json()
    assert data["base_url"] == "https://api.example.com/v1"
    assert data["model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_list_strategies(client):
    resp = await client.get("/api/ai/strategies")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["is_default"] is True


@pytest.mark.asyncio
async def test_create_strategy(client):
    resp = await client.post("/api/ai/strategies", json={
        "name": "Test Strategy",
        "system_prompt": "You are a test analyst.",
    })
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test Strategy"
    assert resp.json()["is_default"] is False


@pytest.mark.asyncio
async def test_set_default_strategy(client):
    create = await client.post("/api/ai/strategies", json={
        "name": "New Default",
        "system_prompt": "New prompt.",
    })
    sid = create.json()["id"]
    resp = await client.post(f"/api/ai/strategies/{sid}/default")
    assert resp.status_code == 200
    assert resp.json()["is_default"] is True


@pytest.mark.asyncio
async def test_get_signals_list(client):
    resp = await client.get("/api/ai/signals")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

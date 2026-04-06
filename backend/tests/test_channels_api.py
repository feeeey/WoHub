import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_create_channel(client):
    resp = await client.post("/api/channels", json={
        "type": "telegram",
        "name": "Test Group",
        "config": {"bot_token": "123:ABC", "chat_id": "-100999"},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] > 0
    assert data["name"] == "Test Group"
    assert data["type"] == "telegram"


@pytest.mark.asyncio
async def test_list_channels(client):
    await client.post("/api/channels", json={
        "type": "telegram",
        "name": "Group A",
        "config": {"bot_token": "t1", "chat_id": "c1"},
    })
    await client.post("/api/channels", json={
        "type": "telegram",
        "name": "Group B",
        "config": {"bot_token": "t2", "chat_id": "c2"},
    })
    resp = await client.get("/api/channels")
    assert resp.status_code == 200
    channels = resp.json()
    assert len(channels) >= 2


@pytest.mark.asyncio
async def test_update_channel(client):
    create = await client.post("/api/channels", json={
        "type": "telegram",
        "name": "Old Name",
        "config": {"bot_token": "t", "chat_id": "c"},
    })
    ch_id = create.json()["id"]

    resp = await client.put(f"/api/channels/{ch_id}", json={
        "name": "New Name",
        "enabled": False,
    })
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"
    assert resp.json()["enabled"] is False


@pytest.mark.asyncio
async def test_delete_channel(client):
    create = await client.post("/api/channels", json={
        "type": "telegram",
        "name": "Temp",
        "config": {"bot_token": "t", "chat_id": "c"},
    })
    ch_id = create.json()["id"]

    resp = await client.delete(f"/api/channels/{ch_id}")
    assert resp.status_code == 200

    listing = await client.get("/api/channels")
    ids = [c["id"] for c in listing.json()]
    assert ch_id not in ids


@pytest.mark.asyncio
async def test_test_channel(client):
    create = await client.post("/api/channels", json={
        "type": "telegram",
        "name": "Test",
        "config": {"bot_token": "fake", "chat_id": "-100"},
    })
    ch_id = create.json()["id"]

    with patch("api.channels.test_channel", return_value={"ok": True, "bot_name": "TestBot"}):
        resp = await client.post(f"/api/channels/{ch_id}/test")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

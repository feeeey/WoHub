import pytest
from agent.config import save_config


@pytest.mark.asyncio
async def test_channel_crud_roundtrip(client):
    async with client as c:
        r = await c.post("/api/agent/channels", json={
            "name": "OpenRouter", "provider": "openai",
            "base_url": "https://openrouter.ai/api/v1", "api_key": "sk-x"})
        assert r.status_code == 200
        cid = r.json()["id"]

        rows = (await c.get("/api/agent/channels")).json()["channels"]
        assert rows[0]["name"] == "OpenRouter" and rows[0]["has_api_key"] is True
        assert "api_key" not in rows[0] and "api_key_enc" not in rows[0]

        r = await c.put(f"/api/agent/channels/{cid}", json={
            "name": "OpenRouter", "provider": "openai", "base_url": "", "api_key": None})
        assert r.status_code == 200
        rows = (await c.get("/api/agent/channels")).json()["channels"]
        assert rows[0]["has_api_key"] is True        # None = 不改 key

        assert (await c.delete(f"/api/agent/channels/{cid}")).status_code == 200
        assert (await c.get("/api/agent/channels")).json()["channels"] == []


@pytest.mark.asyncio
async def test_delete_referenced_channel_409(client):
    async with client as c:
        cid = (await c.post("/api/agent/channels", json={
            "name": "A", "provider": "openai", "base_url": "", "api_key": "k"})).json()["id"]
        save_config({"vision_channel_id": cid})
        assert (await c.delete(f"/api/agent/channels/{cid}")).status_code == 409
        save_config({"vision_channel_id": None})
        assert (await c.delete(f"/api/agent/channels/{cid}")).status_code == 200


@pytest.mark.asyncio
async def test_duplicate_name_409_and_missing_404(client):
    async with client as c:
        body = {"name": "dup", "provider": "openai", "base_url": "", "api_key": "k"}
        await c.post("/api/agent/channels", json=body)
        assert (await c.post("/api/agent/channels", json=body)).status_code == 409
        assert (await c.put("/api/agent/channels/999", json=body)).status_code == 404
        assert (await c.delete("/api/agent/channels/999")).status_code == 404

import pytest
from tests.helpers import save_config_with_channel


@pytest.mark.asyncio
async def test_get_config_defaults(client):
    async with client as c:
        r = await c.get("/api/agent/config")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["channel_id"] is None and body["vision_channel_id"] is None
    assert "api_key" not in body and "main_channel" not in body
    assert body["has_api_key"] is False


@pytest.mark.asyncio
async def test_update_config_roundtrip(client):
    cid = save_config_with_channel()
    async with client as c:
        r = await c.put("/api/agent/config", json={
            "channel_id": cid, "vision_channel_id": None,
            "model": "gpt-5", "vision_model": "gpt-4-vision", "enabled": True,
            "max_tool_calls": 10, "deep_dive_limit": 3,
            "credential_id": None, "max_tokens": 4096})
        assert r.status_code == 200
        body = (await c.get("/api/agent/config")).json()
    assert body["channel_id"] == cid and body["has_api_key"] is True
    assert body["model"] == "gpt-5" and body["vision_model"] == "gpt-4-vision"


def test_load_config_decrypts_key_via_channel():
    from agent.config import load_config
    save_config_with_channel(channel_api_key="k123")
    assert load_config().main_channel.api_key == "k123"


@pytest.mark.asyncio
async def test_put_config_omitted_channel_id_is_noop(client):
    cid = save_config_with_channel()
    async with client as c:
        r = await c.put("/api/agent/config", json={"model": "m2", "enabled": True})
        assert r.status_code == 200
        body = (await c.get("/api/agent/config")).json()
    assert body["channel_id"] == cid and body["model"] == "m2"

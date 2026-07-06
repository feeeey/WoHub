import pytest


@pytest.mark.asyncio
async def test_get_config_defaults(client):
    async with client as c:
        r = await c.get("/api/agent/config")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["provider"] in ("openai", "anthropic")
    assert "api_key" not in body           # 密钥绝不回读
    assert body["has_api_key"] is False


@pytest.mark.asyncio
async def test_update_config_roundtrip_key_masked(client):
    async with client as c:
        r = await c.put("/api/agent/config", json={
            "provider": "openai", "base_url": "https://api.example.com/v1",
            "model": "gpt-5", "vision_model": "gpt-4-vision", "api_key": "sk-secret", "enabled": True,
            "max_tool_calls": 10, "deep_dive_limit": 3,
            "credential_id": None, "max_tokens": 4096})
        assert r.status_code == 200
        r2 = await c.get("/api/agent/config")
    body = r2.json()
    assert body["has_api_key"] is True and "api_key" not in body
    assert body["model"] == "gpt-5" and body["vision_model"] == "gpt-4-vision" and body["enabled"] is True


def test_load_config_decrypts_key():
    from agent.config import save_config, load_config
    save_config({"provider": "anthropic", "model": "claude-sonnet-4-6", "vision_model": "claude-vision",
                 "api_key": "k123", "base_url": "", "enabled": True, "max_tool_calls": 15,
                 "deep_dive_limit": 5, "credential_id": None, "max_tokens": 4096})
    cfg = load_config()
    assert cfg.api_key == "k123" and cfg.provider == "anthropic" and cfg.enabled and cfg.vision_model == "claude-vision"


@pytest.mark.asyncio
async def test_put_without_key_preserves_stored_key(client):
    body = {"provider": "openai", "base_url": "", "model": "m", "vision_model": "",
            "enabled": False, "max_tool_calls": 15, "deep_dive_limit": 5,
            "credential_id": None, "max_tokens": 4096}
    async with client as c:
        await c.put("/api/agent/config", json={**body, "api_key": "sk-keep"})
        r = await c.put("/api/agent/config", json={**body, "api_key": None})
    assert r.json()["has_api_key"] is True


@pytest.mark.asyncio
async def test_put_empty_string_clears_key(client):
    body = {"provider": "openai", "base_url": "", "model": "m", "vision_model": "",
            "enabled": False, "max_tool_calls": 15, "deep_dive_limit": 5,
            "credential_id": None, "max_tokens": 4096}
    async with client as c:
        await c.put("/api/agent/config", json={**body, "api_key": "sk-gone"})
        r = await c.put("/api/agent/config", json={**body, "api_key": ""})
    assert r.json()["has_api_key"] is False

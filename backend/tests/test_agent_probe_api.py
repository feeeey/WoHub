import pytest
from unittest.mock import patch
from tests.helpers import save_config_with_channel


@pytest.mark.asyncio
async def test_models_via_stored_channel(client):
    cid = save_config_with_channel(channel_base_url="https://openrouter.ai/api/v1",
                                   channel_api_key="sk-x")

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"data": [{"id": "deepseek/deepseek-v4-pro"},
                                          {"id": "google/gemini-3-pro"}]}

    with patch("api.agent.requests.get", return_value=FakeResp()) as g:
        async with client as c:
            r = (await c.post("/api/agent/models", json={"channel_id": cid})).json()
    assert r["models"] == ["deepseek/deepseek-v4-pro", "google/gemini-3-pro"]
    assert g.call_args.args[0] == "https://openrouter.ai/api/v1/models"


@pytest.mark.asyncio
async def test_models_inline_overrides_without_saved_channel(client):
    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"data": [{"id": "m1"}]}

    with patch("api.agent.requests.get", return_value=FakeResp()) as g:
        async with client as c:
            r = await c.post("/api/agent/models", json={
                "provider": "openai", "base_url": "https://x.example/v1", "api_key": "sk-t"})
    assert r.json()["models"] == ["m1"]
    assert g.call_args.args[0] == "https://x.example/v1/models"


@pytest.mark.asyncio
async def test_models_requires_key(client):
    async with client as c:
        assert (await c.post("/api/agent/models", json={})).status_code == 400


@pytest.mark.asyncio
async def test_llm_test_per_slot_channels(client):
    from agent.config import save_channel, save_config
    cid = save_config_with_channel(vision_model="")
    vid = save_channel({"name": "视觉专用", "provider": "openai",
                        "base_url": "", "api_key": "sk-v"})
    seen = {}

    def fake_text(channel, model_name):
        seen["text"] = (channel.id, model_name)
        return {"ok": True, "channel": channel.name}

    def fake_vision(channel, model_name):
        seen["vision"] = (channel.id, model_name)
        return {"ok": True, "channel": channel.name, "supports_image": True}

    with patch("api.agent._probe_text", side_effect=fake_text), \
         patch("api.agent._probe_vision", side_effect=fake_vision):
        async with client as c:
            r = (await c.post("/api/agent/test", json={
                "model": "override-m", "vision_channel_id": vid,
                "vision_model": "vm"})).json()
            assert r["main"]["ok"] is True and seen["text"] == (cid, "override-m")
            assert seen["vision"] == (vid, "vm") and r["vision"]["channel"] == "视觉专用"
            # vision_model 显式空 且 配置为空 → vision 为 null
            r2 = (await c.post("/api/agent/test", json={"vision_model": ""})).json()
            assert r2["vision"] is None


@pytest.mark.asyncio
async def test_semantics_get_seeds_and_put_updates(client):
    async with client as c:
        rows = (await c.get("/api/agent/semantics")).json()
        assert len(rows) == 8
        r = await c.put("/api/agent/semantics/oscillator/oversold_zone",
                        json={"bias": "long（改）"})
        assert r.status_code == 200
        rows = (await c.get("/api/agent/semantics")).json()
        target = next(x for x in rows if x["key"] == "oscillator/oversold_zone")
        assert target["bias"] == "long（改）"
        assert (await c.put("/api/agent/semantics/not/exists",
                            json={"bias": "x"})).status_code == 404


@pytest.mark.asyncio
async def test_models_empty_api_key_overrides_stored_key(client):
    """显式 api_key="" 覆盖已存 key → 解析出无 key 渠道 → 400,而非静默用回存储 key。"""
    cid = save_config_with_channel(channel_api_key="sk-real")
    async with client as c:
        r = await c.post("/api/agent/models", json={"channel_id": cid, "api_key": ""})
    assert r.status_code == 400

import pytest
from unittest.mock import patch
from agent.config import save_config


@pytest.mark.asyncio
async def test_models_proxy_openai_compatible(client):
    save_config({"provider": "openai", "base_url": "https://openrouter.ai/api/v1",
                 "model": "m", "api_key": "sk-x", "enabled": False})

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"data": [{"id": "deepseek/deepseek-v4-pro"},
                                          {"id": "google/gemini-3-pro"}]}

    with patch("api.agent.requests.get", return_value=FakeResp()) as g:
        async with client as c:
            r = (await c.post("/api/agent/models", json={})).json()
    assert r["models"] == ["deepseek/deepseek-v4-pro", "google/gemini-3-pro"]
    assert g.call_args.args[0] == "https://openrouter.ai/api/v1/models"


@pytest.mark.asyncio
async def test_models_requires_key(client):
    async with client as c:
        r = await c.post("/api/agent/models", json={})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_llm_test_endpoint_merges_overrides(client):
    save_config({"provider": "openai", "model": "saved-m", "api_key": "k",
                 "vision_model": "", "enabled": False})
    seen = {}

    def fake_probe_text(cfg):
        seen["model"] = cfg.model
        return {"ok": True}

    with patch("api.agent._probe_text", side_effect=fake_probe_text), \
         patch("api.agent._probe_vision", return_value={"ok": True, "supports_image": True}) as pv:
        async with client as c:
            r = (await c.post("/api/agent/test",
                              json={"model": "override-m", "vision_model": "vm"})).json()
            assert r["main"]["ok"] is True and seen["model"] == "override-m"
            assert r["vision"]["supports_image"] is True
            # 不传 vision_model 且配置为空 → vision 为 null
            with patch("api.agent._probe_text", return_value={"ok": True}):
                r2 = (await c.post("/api/agent/test", json={})).json()
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

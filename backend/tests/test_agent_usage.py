import pytest

from agent.chat import store


@pytest.mark.asyncio
async def test_usage_aggregates_tokens_and_tools(client):
    sid = store.create_session()
    store.add_message(sid, "user", "q1")
    store.add_message(sid, "assistant", "a1", model="m1",
                      input_tokens=1000, output_tokens=200,
                      trace={"steps": [
                          {"tool": "get_klines", "args": {}, "result": '{"ok": 1}'},
                          {"tool": "get_klines", "args": {}, "result": '{"error": "x"}'},
                          {"tool": "market_overview", "args": {}, "result": '{"ok": 1}'}]})
    store.add_message(sid, "assistant", "a2", model="m2",
                      input_tokens=500, output_tokens=100)

    resp = await client.get("/api/agent/usage")
    assert resp.status_code == 200
    d = resp.json()

    assert d["totals"] == {"turns": 2, "input_tokens": 1500, "output_tokens": 300}
    assert len(d["daily"]) == 1 and d["daily"][0]["turns"] == 2

    models = {m["model"]: m for m in d["by_model"]}
    assert models["m1"]["input_tokens"] == 1000
    assert models["m2"]["output_tokens"] == 100

    tools = {t["tool"]: t for t in d["tools"]}
    assert tools["get_klines"]["calls"] == 2
    assert tools["get_klines"]["errors"] == 1
    assert tools["get_klines"]["error_rate"] == 0.5
    assert tools["market_overview"]["errors"] == 0


@pytest.mark.asyncio
async def test_usage_empty_db(client):
    resp = await client.get("/api/agent/usage")
    d = resp.json()
    assert d["totals"]["turns"] == 0
    assert d["daily"] == [] and d["tools"] == []


@pytest.mark.asyncio
async def test_usage_window_clamps(client):
    resp = await client.get("/api/agent/usage?days=9999")
    assert resp.json()["window_days"] == 365

import pytest

from agent import memory
from agent.chat.prompts import build_system_prompt


def test_save_list_forget_roundtrip():
    out = memory.save_memory("只做 4h 以上级别", "preference")
    mid = out["id"]
    rows = memory.list_memories()
    assert len(rows) == 1 and rows[0]["content"] == "只做 4h 以上级别"

    assert memory.forget_memory(mid) == {"ok": True, "deleted": mid}
    assert memory.list_memories() == []


def test_save_rejects_empty_and_overlong():
    assert "error" in memory.save_memory("   ")
    assert "error" in memory.save_memory("x" * 201)


def test_save_dedupes_identical_content():
    a = memory.save_memory("不碰 meme 币")
    b = memory.save_memory("不碰 meme 币")
    assert b["id"] == a["id"] and "已存在" in b["note"]
    assert len(memory.list_memories()) == 1


def test_save_enforces_count_cap(monkeypatch):
    monkeypatch.setattr(memory, "MAX_MEMORIES", 3)
    for i in range(3):
        memory.save_memory(f"记忆{i}")
    out = memory.save_memory("第四条")
    assert "error" in out and "forget" in out["error"]


def test_unknown_category_falls_back():
    memory.save_memory("测试", category="banana")
    assert memory.list_memories()[0]["category"] == "preference"


def test_forget_bad_inputs():
    assert "error" in memory.forget_memory("abc")
    assert "error" in memory.forget_memory(9999)


def test_memory_injected_into_system_prompt():
    memory.save_memory("只做 4h 以上级别", "preference")
    memory.save_memory("主要交易 BTC 和 ETH", "fact")
    prompt = build_system_prompt()
    assert "【长期记忆】" in prompt
    assert "只做 4h 以上级别" in prompt
    assert "·事实" in prompt and "·偏好" in prompt


def test_prompt_has_no_memory_block_when_empty():
    assert "【长期记忆】" not in build_system_prompt()


@pytest.mark.asyncio
async def test_memory_api_list_and_delete(client):
    memory.save_memory("api 测试记忆")
    resp = await client.get("/api/agent/memories")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1 and data["max"] == memory.MAX_MEMORIES
    mid = data["memories"][0]["id"]

    assert (await client.delete(f"/api/agent/memories/{mid}")).status_code == 200
    assert (await client.delete(f"/api/agent/memories/{mid}")).status_code == 404
    assert (await client.get("/api/agent/memories")).json()["count"] == 0

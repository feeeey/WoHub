"""一轮卡住不能拖垮整个 agent。

worker 单线程串行 drain chat_turns，run_turn 里唯一的取消检查点在流式数据块
之间——模型挂起、不再发块时永远检查不到。没有外层墙钟上限的话，worker 线程
永久阻塞，之后所有轮次（含自动简评）无限排队，而且没有任何告警。
"""
import asyncio

import pytest

from agent.chat import runtime, store
from config import settings
from database import get_db
from tests.helpers import save_config_with_channel


def _queued_turn(text="卡住吧"):
    sid = store.create_session("t")
    mid = store.add_message(sid, "user", text)
    store.create_turn(sid, mid)
    return store.claim_next_turn()


def test_timeout_wraps_the_drive_call():
    async def hang(*a, **kw):
        await asyncio.sleep(10)

    async def go():
        with pytest.raises(runtime.TurnTimeout):
            await runtime._drive_with_timeout(None, "p", None, None, None,
                                              timeout_s=0.05)

    orig, runtime._drive = runtime._drive, hang
    try:
        asyncio.run(go())
    finally:
        runtime._drive = orig


def test_hung_turn_reaches_a_terminal_state(monkeypatch):
    """核心回归：轮次必须落到 failed，不能悬在 running。"""
    save_config_with_channel()
    monkeypatch.setattr(runtime, "TURN_TIMEOUT_S", 0.05)

    async def hang(*a, **kw):
        await asyncio.sleep(30)

    monkeypatch.setattr(runtime, "_drive", hang)
    monkeypatch.setattr(runtime, "_build_prompt",
                        lambda *a, **kw: ("prompt", {"content": "x"}))
    monkeypatch.setattr(runtime, "_build_agent", lambda cfg, model: object())
    monkeypatch.setattr(runtime, "build_model", lambda ch, m: object())

    row = _queued_turn()
    runtime.run_turn(row)

    db = get_db(settings.db_path)
    turn = db.execute("SELECT status FROM chat_turns WHERE id = ?", (row["id"],)).fetchone()
    db.close()
    assert turn["status"] == "failed", "超时的轮次必须有终态，否则 worker 视角永远在跑"


def test_timeout_is_reported_to_the_user(monkeypatch):
    save_config_with_channel()
    monkeypatch.setattr(runtime, "TURN_TIMEOUT_S", 0.05)

    async def hang(*a, **kw):
        await asyncio.sleep(30)

    monkeypatch.setattr(runtime, "_drive", hang)
    monkeypatch.setattr(runtime, "_build_prompt",
                        lambda *a, **kw: ("prompt", {"content": "x"}))
    monkeypatch.setattr(runtime, "_build_agent", lambda cfg, model: object())
    monkeypatch.setattr(runtime, "build_model", lambda ch, m: object())

    row = _queued_turn()
    runtime.run_turn(row)

    msgs = store.list_messages(row["session_id"])
    assert msgs[-1]["role"] == "assistant" and msgs[-1]["error"], "要留下可见的错误消息"
    assert "上限" in msgs[-1]["error"]

    events = store.list_events(row["id"]) if hasattr(store, "list_events") else []
    if events:
        assert any(e["type"] == "turn_error" for e in events)


def test_normal_turns_are_unaffected():
    """默认上限要足够容纳正常长轮次（多次工具调用 + 截图 + 视觉分析）。"""
    assert runtime.TURN_TIMEOUT_S >= 600

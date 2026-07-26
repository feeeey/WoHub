"""会话摘要压缩 + 证据回灌测试。压缩器（LLM 调用）用 monkeypatch 替换。"""
from unittest.mock import patch

import pytest

from agent.chat import runtime, store
from agent.chat.prompts import render_history
from agent.chat.runtime import HISTORY_LIMIT, _build_prompt, _maybe_compress
from agent import tools as T


def _deps():
    return runtime.ChatDeps(turn_id=0, budget=T.ToolBudget(), credential_id=None)


class _NoopDeps(runtime.ChatDeps):
    def emit(self, type_, payload):   # turn_id=0 没有对应 turn 行，事件只收不落库
        pass


def _seed_session(n_messages: int):
    """交替 user/assistant 造 n 条消息，返回 (session_id, 最后的 user 消息 id)。"""
    sid = store.create_session()
    last_user = None
    for i in range(n_messages):
        role = "user" if i % 2 == 0 else "assistant"
        mid = store.add_message(sid, role, f"消息{i}：讨论 BTCUSDT 的第 {i} 个话题")
        if role == "user":
            last_user = mid
    return sid, last_user


def test_no_compress_within_window():
    sid, last_user = _seed_session(10)
    deps = _NoopDeps(turn_id=0, budget=T.ToolBudget(), credential_id=None)
    with patch.object(runtime, "_summarize") as summ:
        prompt, _ = _build_prompt(sid, last_user, None, deps, model=object())
    summ.assert_not_called()
    assert "会话早期摘要" not in prompt


def test_compress_triggers_beyond_window_and_stores_cursor():
    sid, last_user = _seed_session(HISTORY_LIMIT + 8)
    deps = _NoopDeps(turn_id=0, budget=T.ToolBudget(), credential_id=None)

    with patch.object(runtime, "_summarize", return_value="早期讨论了 BTCUSDT 多个话题") as summ:
        prompt, _ = _build_prompt(sid, last_user, None, deps, model=object())

    summ.assert_called_once()
    assert "【会话早期摘要】" in prompt and "早期讨论了 BTCUSDT" in prompt
    sess = store.get_session(sid)
    assert sess["summary"] == "早期讨论了 BTCUSDT 多个话题"
    assert sess["summary_upto"] > 0
    # 压缩过程作为一步 trace 呈现
    assert any(s["tool"] == "history_compress" for s in deps.trace)


def test_compress_is_incremental():
    """第二次构建 prompt 时溢出集未增长 → 零成本复用已有摘要。"""
    sid, last_user = _seed_session(HISTORY_LIMIT + 8)
    deps = _NoopDeps(turn_id=0, budget=T.ToolBudget(), credential_id=None)

    with patch.object(runtime, "_summarize", return_value="v1 摘要") as summ:
        _build_prompt(sid, last_user, None, deps, model=object())
        assert summ.call_count == 1
        prompt2, _ = _build_prompt(sid, last_user, None, deps, model=object())
        assert summ.call_count == 1          # 游标已覆盖，不再调用
    assert "v1 摘要" in prompt2


def test_compress_failure_degrades_to_old_summary():
    sid, last_user = _seed_session(HISTORY_LIMIT + 8)
    store.update_session_summary(sid, "旧摘要仍然可用", 0)   # 游标 0 → 会尝试压缩
    deps = _NoopDeps(turn_id=0, budget=T.ToolBudget(), credential_id=None)

    with patch.object(runtime, "_summarize", side_effect=RuntimeError("LLM 超时")):
        prompt, _ = _build_prompt(sid, last_user, None, deps, model=object())

    assert "旧摘要仍然可用" in prompt          # 降级但不中断
    assert store.get_session(sid)["summary_upto"] == 0   # 游标不前进 → 下轮重试


def test_no_model_no_compress():
    """model=None（异常路径的兜底调用）时跳过压缩，不炸。"""
    sid, last_user = _seed_session(HISTORY_LIMIT + 8)
    deps = _NoopDeps(turn_id=0, budget=T.ToolBudget(), credential_id=None)
    prompt, _ = _build_prompt(sid, last_user, None, deps)
    assert "会话早期摘要" not in prompt


# ---- 证据回灌 ----

def _msg(mid, role, content, steps=None):
    return {"id": mid, "role": role, "content": content,
            "trace": {"steps": steps} if steps else None}


def test_render_history_feeds_evidence_for_recent_assistant():
    steps = [{"tool": "kline_structure", "args": {"symbol": "BTCUSDT"},
              "result": '{"atr": 420.5, "pivot_below": 63590}'}]
    msgs = [_msg(1, "user", "看下 BTC"),
            _msg(2, "assistant", "结构偏多", steps),
            _msg(3, "user", "那 ETH 呢")]
    out = render_history(msgs)
    assert "本轮已取证据" in out
    assert "kline_structure" in out and "420.5" in out


def test_render_history_evidence_only_last_k():
    steps = [{"tool": "get_klines", "args": {}, "result": "{}"}]
    msgs = []
    for i in range(1, 9, 2):
        msgs.append(_msg(i, "user", f"问题{i}"))
        msgs.append(_msg(i + 1, "assistant", f"回答{i}", steps))
    out = render_history(msgs)
    # 4 条 assistant 都带 trace，但只有最近 EVIDENCE_LAST_K=2 条附证据
    assert out.count("本轮已取证据") == 2


def test_render_history_caps_steps_per_message():
    steps = [{"tool": f"t{i}", "args": {}, "result": "r"} for i in range(6)]
    msgs = [_msg(1, "user", "q"), _msg(2, "assistant", "a", steps)]
    out = render_history(msgs)
    assert "另 3 步略" in out

from agent.chat import semantics
from agent.chat.prompts import build_system_prompt, render_history


def test_seed_defaults_idempotent_and_complete():
    n = semantics.seed_defaults()
    assert n == 8
    assert semantics.seed_defaults() == 0          # 二次幂等
    rows = semantics.get_all()
    assert len(rows) == 8
    keys = {r["key"] for r in rows}
    assert "oscillator/divergence_bottom" in keys and "trend/shadows" in keys
    assert all(r["meaning"] for r in rows)          # 初稿无空 meaning


def test_upsert_validates_key():
    semantics.seed_defaults()
    assert semantics.upsert("oscillator/oversold_zone", {"bias": "long（超跌反弹）"}) is True
    assert semantics.get_all_map()["oscillator/oversold_zone"]["bias"] == "long（超跌反弹）"
    assert semantics.upsert("not/exists", {"bias": "x"}) is False


def test_system_prompt_injects_semantics_and_rules():
    semantics.seed_defaults()
    sp = build_system_prompt()
    assert "底背离" in sp and "顶背离" in sp          # 语义档案已注入
    assert "不下单" in sp or "不能下单" in sp          # 红线句
    assert "纯技术分析" in sp
    assert "UTC" in sp                                # 时间戳


def test_render_history_feeds_evidence_for_recent_assistant_only():
    """记忆层契约（chat-v2 起）：最近 EVIDENCE_LAST_K 条助手消息回灌工具证据
    摘要（免重复取数），更早的只回灌正文（token 纪律）。"""
    from agent.chat.prompts import EVIDENCE_LAST_K
    old_steps = [{"tool": "old_tool_call", "args": {}, "result": "{}"}]
    new_steps = [{"tool": "get_klines", "args": {}, "result": '{"last": 64375}'}]
    msgs = [{"id": 1, "role": "user", "content": "早期问题", "trace": None},
            {"id": 2, "role": "assistant", "content": "早期回答",
             "trace": {"steps": old_steps}}]
    # 塞满 K 条更近的助手消息，把 id=2 挤出证据窗口
    mid = 3
    for _ in range(EVIDENCE_LAST_K):
        msgs.append({"id": mid, "role": "user", "content": f"问题{mid}", "trace": None})
        msgs.append({"id": mid + 1, "role": "assistant", "content": f"回答{mid + 1}",
                     "trace": {"steps": new_steps}})
        mid += 2
    text = render_history(msgs)
    assert "早期问题" in text and "早期回答" in text
    assert "get_klines" in text                # 近期证据回灌
    assert "old_tool_call" not in text         # 窗口外的不回灌

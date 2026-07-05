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


def test_render_history_skips_trace_and_orders_by_id():
    msgs = [
        {"id": 1, "role": "user", "content": "看下 BTC", "trace": None},
        {"id": 2, "role": "assistant", "content": "BTC 结构偏多",
         "trace": {"steps": [{"tool": "get_klines"}]}},
    ]
    text = render_history(msgs)
    assert "看下 BTC" in text and "BTC 结构偏多" in text
    assert "get_klines" not in text                   # 工具轨迹不回灌

"""自动简评测试：护栏逐条验证 + 完成后推送 + executor 集成。"""
import json
from unittest.mock import patch

import pytest
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent import digest
from agent.chat import runtime, store
from config import settings
from database import get_db
from tests.helpers import save_config_with_channel


def _make_channel(type_="telegram"):
    db = get_db(settings.db_path)
    cur = db.execute("INSERT INTO channels (type, name, config_json) VALUES (?, 'ch', ?)",
                     (type_, json.dumps({"bot_token": "t", "chat_id": "c"})))
    db.commit()
    cid = cur.lastrowid
    db.close()
    return {"id": cid, "type": type_, "config": {"bot_token": "t", "chat_id": "c"}}


def _make_task(name="监控任务"):
    db = get_db(settings.db_path)
    cur = db.execute("INSERT INTO tasks (name, type) VALUES (?, 'watchlist_signal')", (name,))
    db.commit()
    tid = cur.lastrowid
    db.close()
    return tid


# ---- 护栏 ----

def test_enqueue_creates_turn_with_push_channel():
    save_config_with_channel()
    tid = _make_task("底背离监控")
    ch = _make_channel()

    out = digest.enqueue_digest(tid, "BTCUSDT → 底背离(1h)", ch)
    assert "queued" in out

    sessions = store.list_sessions()
    dsess = next(s for s in sessions if s["title"] == digest.DIGEST_SESSION_TITLE)
    msgs = store.list_messages(dsess["id"])
    assert msgs[0]["content"].startswith(f"{digest.DIGEST_PREFIX} 任务#{tid}")
    assert "底背离监控" in msgs[0]["content"]
    assert "后验统计" in msgs[0]["content"]        # 简评要求引用数据

    db = get_db(settings.db_path)
    row = db.execute("SELECT push_channel_id FROM chat_turns WHERE id = ?",
                     (out["queued"],)).fetchone()
    db.close()
    assert row["push_channel_id"] == ch["id"]


def test_skip_when_agent_disabled():
    out = digest.enqueue_digest(_make_task(), "信号", _make_channel())
    assert "未启用" in out["skipped"]


def test_cooldown_suppresses_same_task():
    save_config_with_channel(cooldown_minutes=240)
    tid = _make_task()
    ch = _make_channel()
    assert "queued" in digest.enqueue_digest(tid, "第一次", ch)
    # 消耗掉 queued turn，避免防堆积护栏先触发，单独考核冷却
    row = store.claim_next_turn()
    store.finish_turn(row["id"], "done")
    out2 = digest.enqueue_digest(tid, "第二次", ch)
    assert "冷却" in out2["skipped"]
    # 不同任务不受影响
    assert "queued" in digest.enqueue_digest(_make_task("另一个"), "信号", ch)


def test_cooldown_does_not_leak_across_task_id_prefixes():
    """任务 #1 的冷却查询不能被任务 #12 的记录命中。

    冷却用 content LIKE 查历史触发消息；早期模式是 '任务#1%'，会匹配到
    '任务#12《…》'，导致低编号任务的简评被静默跳过——静默正是最难发现的
    故障形态。"""
    save_config_with_channel(cooldown_minutes=240)
    ch = _make_channel()
    tids = [_make_task(f"任务{i}") for i in range(1, 13)]
    first, twelfth = tids[0], tids[11]
    assert str(twelfth).startswith(str(first)), "本用例需要 #N 与 #N* 的前缀关系"

    # 只有 #12 触发过简评
    assert "queued" in digest.enqueue_digest(twelfth, "十二号信号", ch)
    row = store.claim_next_turn()
    store.finish_turn(row["id"], "done")

    # #1 从未触发过，不该被判为冷却中
    out = digest.enqueue_digest(first, "一号信号", ch)
    assert "queued" in out, f"任务 #{first} 被任务 #{twelfth} 的记录误判冷却：{out}"


def test_cooldown_matches_task_name_wildcards_literally():
    """任务名里的 % / _ 不能被当成 LIKE 通配符。"""
    save_config_with_channel(cooldown_minutes=240)
    ch = _make_channel()
    tid = _make_task("涨幅%监控_A")
    assert "queued" in digest.enqueue_digest(tid, "信号", ch)
    row = store.claim_next_turn()
    store.finish_turn(row["id"], "done")
    assert "冷却" in digest.enqueue_digest(tid, "再次", ch)["skipped"]


def test_no_pileup_when_turn_pending():
    save_config_with_channel(cooldown_minutes=0)
    ch = _make_channel()
    assert "queued" in digest.enqueue_digest(_make_task(), "一", ch)
    out = digest.enqueue_digest(_make_task("b"), "二", ch)
    assert "不堆积" in out["skipped"]


def test_enqueue_never_raises():
    with patch("agent.config.load_config", side_effect=RuntimeError("db 炸了")):
        out = digest.enqueue_digest(1, "x", None)
    assert "skipped" in out


# ---- 完成后推送 ----

def test_turn_completion_pushes_to_channel():
    save_config_with_channel(cooldown_minutes=0)
    ch = _make_channel()
    tid = _make_task("推送验证")
    digest.enqueue_digest(tid, "BTCUSDT → 底背离(1h)", ch)
    row = store.claim_next_turn()
    assert row["push_channel_id"] == ch["id"]

    async def stream_fn(messages, info: AgentInfo):
        yield "简评：这批信号可信度一般，底背离近90天4h上涨占比42.3%，建议观望。"

    with patch("channels.sender.send_text", return_value=1) as st:
        runtime.run_turn(row, model_override=FunctionModel(stream_function=stream_fn))

    st.assert_called_once()
    pushed = st.call_args.args[2]
    assert pushed.startswith("🤖 AI 简评")
    assert "42.3%" in pushed

    db = get_db(settings.db_path)
    log = db.execute("SELECT * FROM push_logs ORDER BY id DESC LIMIT 1").fetchone()
    db.close()
    assert log["channel_id"] == ch["id"] and log["status"] == "success"


def test_push_failure_does_not_fail_turn():
    save_config_with_channel(cooldown_minutes=0)
    ch = _make_channel()
    digest.enqueue_digest(_make_task(), "信号", ch)
    row = store.claim_next_turn()

    async def stream_fn(messages, info: AgentInfo):
        yield "简评内容。"

    with patch("channels.sender.send_text", side_effect=RuntimeError("bot 挂了")):
        runtime.run_turn(row, model_override=FunctionModel(stream_function=stream_fn))

    db = get_db(settings.db_path)
    turn = db.execute("SELECT status FROM chat_turns WHERE id = ?", (row["id"],)).fetchone()
    log = db.execute("SELECT status FROM push_logs ORDER BY id DESC LIMIT 1").fetchone()
    db.close()
    assert turn["status"] == "done"            # 推送失败不改轮次终态
    assert log["status"] == "failed"           # 但失败有记录


def test_ordinary_turns_do_not_push():
    save_config_with_channel()
    sid = store.create_session()
    mid = store.add_message(sid, "user", "看下 BTC")
    store.create_turn(sid, mid)                # 普通轮次：push_channel_id 为 NULL
    row = store.claim_next_turn()

    async def stream_fn(messages, info: AgentInfo):
        yield "好的。"

    with patch("channels.sender.send_text") as st:
        runtime.run_turn(row, model_override=FunctionModel(stream_function=stream_fn))
    st.assert_not_called()


# ---- executor 集成 ----

def test_executor_calls_digest_when_action_enabled():
    from tasks import executor
    with patch("agent.digest.enqueue_digest", return_value={"queued": 1}) as eq, \
         patch("tasks.executor.run_screener", return_value=["BINANCE:BTCUSDT.P"]):
        executor._exec_watchlist_signal(
            1, {"screeners": [{"folder_type": "oscillator",
                               "screener_name": "oversold_zone", "label": "超卖"}],
                "resolutions": ["1h"]},
            ["text_summary", "agent_digest"], None)
    eq.assert_called_once()
    assert eq.call_args.args[0] == 1


def test_executor_skips_digest_without_signals():
    from tasks import executor
    with patch("agent.digest.enqueue_digest") as eq, \
         patch("tasks.executor.run_screener", return_value=[]):
        executor._exec_watchlist_signal(
            1, {"screeners": [{"folder_type": "oscillator",
                               "screener_name": "oversold_zone", "label": "超卖"}],
                "resolutions": ["1h"]},
            ["agent_digest"], None)
    eq.assert_not_called()

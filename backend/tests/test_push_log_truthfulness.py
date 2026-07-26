"""push_logs 必须说实话。

`_send_push` 早期吞掉异常，`_log_push` 紧随其后按默认的 `status='success'` 落库。
结果是推送审计在**最关键的故障**上恒显示正常：信号根本没送达用户，历史里却是
一片绿色的成功记录。现在发送与记录绑在 `_push_and_log` 里，按真实结果写。
"""
import json
from unittest.mock import patch

import pytest

from config import settings
from database import get_db
from tasks import executor


def _channel():
    db = get_db(settings.db_path)
    cid = db.execute("INSERT INTO channels (type, name, config_json) VALUES "
                     "('telegram', 'tg', ?)",
                     (json.dumps({"bot_token": "t", "chat_id": "c"}),)).lastrowid
    db.commit()
    db.close()
    return {"id": cid, "name": "tg", "type": "telegram",
            "config": {"bot_token": "t", "chat_id": "c"}}


def _task():
    db = get_db(settings.db_path)
    tid = db.execute("INSERT INTO tasks (name, type) VALUES ('t', 'watchlist_signal')").lastrowid
    db.commit()
    db.close()
    return tid


def _logs(task_id):
    db = get_db(settings.db_path)
    try:
        return [dict(r) for r in db.execute(
            "SELECT status, error_message FROM push_logs WHERE task_id = ?", (task_id,))]
    finally:
        db.close()


def test_failed_push_is_recorded_as_failed():
    tid, ch = _task(), _channel()
    with patch("tasks.executor.send_text", side_effect=RuntimeError("Telegram 429")):
        ok = executor._push_and_log(tid, ch, "信号来了")

    assert ok is False
    rows = _logs(tid)
    assert len(rows) == 1
    assert rows[0]["status"] == "failed", "推送失败却记成了 success —— 审计在说谎"
    assert "429" in (rows[0]["error_message"] or "")


def test_successful_push_is_recorded_as_success():
    tid, ch = _task(), _channel()
    with patch("tasks.executor.send_text", return_value=None):
        assert executor._push_and_log(tid, ch, "信号来了") is True
    rows = _logs(tid)
    assert rows and rows[0]["status"] == "success" and not rows[0]["error_message"]


def test_push_failure_does_not_abort_the_task():
    """推送挂了不能连带毁掉信号落库与截图——只是记成 failed。"""
    tid, ch = _task(), _channel()
    with patch("tasks.executor.send_text", side_effect=RuntimeError("boom")), \
         patch("tasks.executor.run_screener", return_value=["BINANCE:BTCUSDT.P"]), \
         patch("tasks.executor.record_snapshot"), \
         patch("tasks.executor.schedule_outcome_tracking"):
        executor._exec_watchlist_signal(
            tid,
            {"screeners": [{"folder_type": "oscillator", "screener_name": "divergence",
                            "label": "底背离"}],
             "resolutions": ["1h"], "watchlist_id": 0, "overlap_threshold": 1},
            ["text_summary"], ch)

    assert _logs(tid)[0]["status"] == "failed"
    db = get_db(settings.db_path)
    try:
        n = db.execute("SELECT COUNT(*) c FROM signals WHERE task_id = ?",
                       (tid,)).fetchone()["c"]
    finally:
        db.close()
    assert n == 1, "推送失败不应影响信号落库"


def test_send_push_reports_outcome_instead_of_swallowing():
    ch = _channel()
    with patch("tasks.executor.send_text", side_effect=RuntimeError("x")):
        assert executor._send_push(ch, "m") == (False, "x")
    with patch("tasks.executor.send_text", return_value=None):
        assert executor._send_push(ch, "m") == (True, None)

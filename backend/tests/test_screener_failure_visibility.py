"""被限流的一轮不能长得像「行情平静」。

run_screener 早期在重试耗尽后 return []，与「跑成了但 0 命中」完全同形：
推送里显示 0 命中，用户以为没机会，实际根本没查到。现在它抛
ScreenerUnavailable，executor 分开统计并把失败明细带进消息与 push_logs。
"""
from unittest.mock import MagicMock, patch

import pytest

from config import settings
from database import get_db
from sources.pine_screener import ScreenerUnavailable, run_screener
from tasks import executor


def _resp(text):
    r = MagicMock()
    r.text = text
    r.raise_for_status = MagicMock()
    return r


# ---- 数据源层：限流必须可辨认 ----------------------------------------------

@pytest.mark.parametrize("body", [
    '{"error":"request_limit_reached"}',
    '{"error":"internal_error"}',
])
def test_exhausted_retries_raise_instead_of_empty_list(body):
    with patch("sources.pine_screener._get_session") as sess, \
         patch("sources.pine_screener.time.sleep"), \
         patch("sources.pine_screener._wait_rate_limit"):
        sess.return_value.post.return_value = _resp(body)
        with pytest.raises(ScreenerUnavailable):
            run_screener("oscillator", "divergence", "1h", 1)


def test_genuine_empty_result_is_still_an_empty_list():
    """真的没命中仍然是空列表——不能把两种情况混成一种。"""
    with patch("sources.pine_screener._get_session") as sess, \
         patch("sources.pine_screener._wait_rate_limit"):
        sess.return_value.post.return_value = _resp('{"snapshot":{"symbols":[]}}')
        assert run_screener("oscillator", "divergence", "1h", 1) == []


# ---- executor：失败与空结果分流 --------------------------------------------

SCREENERS = [{"folder_type": "oscillator", "screener_name": "divergence",
              "label": "顶底背离"}]


def test_run_screeners_separates_failures_from_hits():
    def fake(folder, name, res, wl):
        if res == "1h":
            raise ScreenerUnavailable("限流")
        return ["BINANCE:BTCUSDT.P"]

    with patch("tasks.executor.run_screener", side_effect=fake):
        results, failures = executor._run_screeners(1, SCREENERS, ["1h", "4h"], 0)

    assert failures == ["顶底背离(1h)"]
    assert [r["resolution"] for r in results] == ["4h"]


def test_failure_note_warns_against_reading_it_as_quiet():
    note = executor._failure_note(["顶底背离(1h)"])
    assert "未取到结果" in note and "行情平静" in note
    assert executor._failure_note([]) == ""


def test_total_failure_is_logged_as_a_failed_push_not_silence():
    """全部筛选器挂掉时，push_logs 必须留下 failed 记录。"""
    db = get_db(settings.db_path)
    tid = db.execute("INSERT INTO tasks (name, type) VALUES ('t', 'watchlist_signal')").lastrowid
    db.commit()
    db.close()

    with patch("tasks.executor.run_screener", side_effect=ScreenerUnavailable("限流")):
        executor._exec_watchlist_signal(
            tid, {"screeners": SCREENERS, "resolutions": ["1h"], "watchlist_id": 0},
            ["text_summary"], None)

    db = get_db(settings.db_path)
    rows = db.execute("SELECT status, content_text FROM push_logs WHERE task_id = ?",
                      (tid,)).fetchall()
    db.close()
    assert rows and rows[0]["status"] == "failed"
    assert "并非行情平静" in rows[0]["content_text"]
    assert "并非行情平静" in executor.get_last_result(tid)["message"]


def test_partial_failure_is_appended_to_the_pushed_message():
    """部分失败时仍要推送命中结果，但消息里必须说明结果不完整。"""
    db = get_db(settings.db_path)
    tid = db.execute("INSERT INTO tasks (name, type) VALUES ('t2', 'watchlist_signal')").lastrowid
    db.commit()
    db.close()

    two = SCREENERS + [{"folder_type": "trend", "screener_name": "shadows",
                        "label": "长影线"}]

    def fake(folder, name, res, wl):
        if name == "shadows":
            raise ScreenerUnavailable("限流")
        return ["BINANCE:BTCUSDT.P"]

    with patch("tasks.executor.run_screener", side_effect=fake), \
         patch("tasks.executor.record_snapshot"), \
         patch("tasks.executor.schedule_outcome_tracking"):
        executor._exec_watchlist_signal(
            tid, {"screeners": two, "resolutions": ["1h"], "watchlist_id": 0,
                  "overlap_threshold": 1},
            [], None)

    msg = executor.get_last_result(tid)["message"]
    assert "BTCUSDT" in msg, "命中结果仍要照常呈现"
    assert "长影线(1h)" in msg and "结果不完整" in msg

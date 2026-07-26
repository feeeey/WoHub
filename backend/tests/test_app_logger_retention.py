"""故障证据不该被调试噪声冲掉。

一次 run_screener 就写 4~5 条 info/debug，几个任务跑一轮上百条。单一 200 条环形
缓冲区里，错误信息几分钟就被挤没了，而排障恰恰发生在事后。
"""
import app_logger
from app_logger import PROBLEM_CAPACITY, STREAM_CAPACITY, clear_logs, get_logs, log


def setup_function():
    clear_logs()


def test_error_survives_a_flood_of_debug_noise():
    log("scheduler", "error", "任务 7 漏跑：Run time of job was missed")
    for i in range(STREAM_CAPACITY + 200):     # 冲掉整条流水缓冲区
        log("pine_screener", "debug", f"Response lines: {i}")

    errors = [e for e in get_logs(level="error", limit=50)
              if "漏跑" in e["message"]]
    assert errors, "错误被日常流水冲掉了——这正是排障时最需要的那一条"


def test_warnings_are_retained_too():
    log("pine_screener", "warn", "未配置 TradingView 登录 Cookie")
    for i in range(STREAM_CAPACITY + 10):
        log("executor", "info", f"noise {i}")
    assert any("Cookie" in e["message"] for e in get_logs(level="warn", limit=50))


def test_no_duplicates_when_an_entry_is_in_both_buffers():
    log("scheduler", "error", "只此一条")
    hits = [e for e in get_logs(limit=200) if e["message"] == "只此一条"]
    assert len(hits) == 1, f"同一条记录被返回了 {len(hits)} 次"


def test_newest_first_ordering_across_buffers():
    log("a", "error", "早")
    log("b", "info", "晚")
    msgs = [e["message"] for e in get_logs(limit=10)]
    assert msgs.index("晚") < msgs.index("早")


def test_internal_sequence_field_is_not_exposed():
    log("a", "info", "x")
    assert "_seq" not in get_logs(limit=1)[0]


def test_problem_buffer_is_itself_bounded():
    for i in range(PROBLEM_CAPACITY + 50):
        log("x", "error", f"e{i}")
    assert len(app_logger._problems) == PROBLEM_CAPACITY


def test_source_and_level_filters_still_work():
    log("scheduler", "error", "sched fail")
    log("executor", "error", "exec fail")
    log("executor", "info", "exec ok")
    assert [e["message"] for e in get_logs(source="executor", level="error")] == \
        ["exec fail"]

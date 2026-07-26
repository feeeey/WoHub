import os
from unittest.mock import patch

import pytest

from config import settings
from database import get_db
from screenshots import service


@pytest.fixture
def shots_dir(tmp_path, monkeypatch):
    d = tmp_path / "shots"
    d.mkdir()
    monkeypatch.setattr(settings, "screenshots_dir", str(d))
    return d


def _make_file(d, name):
    p = d / name
    p.write_bytes(b"\x89PNG fake")
    return p


def _chartshot(files):
    return patch("screenshots.client.chartshot_client.screenshot",
                 return_value={"ok": True, "files": files})


def _make_task(name="t", type_="watchlist_signal"):
    """screenshots.task_id 有外键约束，落库前任务必须真实存在。"""
    db = get_db(settings.db_path)
    cur = db.execute("INSERT INTO tasks (name, type) VALUES (?, ?)", (name, type_))
    db.commit()
    tid = cur.lastrowid
    db.close()
    return tid


# --- symbol / timeframe 归一化 ---

def test_normalize_symbol_strips_binance_prefix_and_perp_suffix():
    assert service.normalize_symbol("BINANCE:BTCUSDT.P") == "BTCUSDT"
    assert service.normalize_symbol("btcusdt") == "BTCUSDT"
    assert service.normalize_symbol("  ETHUSDT  ") == "ETHUSDT"


def test_normalize_symbol_keeps_other_exchange_prefix():
    """ChartShot 自己认 'OANDA:XAUUSD'，剥掉前缀反而会被当成币安永续。"""
    assert service.normalize_symbol("OANDA:XAUUSD") == "OANDA:XAUUSD"


def test_normalize_timeframes_dedupes_and_lowercases():
    assert service.normalize_timeframes(["1H", "4h", "1h"]) == ["1h", "4h"]


def test_normalize_timeframes_defaults_when_empty():
    assert service.normalize_timeframes(None) == list(service.DEFAULT_TIMEFRAMES)
    assert service.normalize_timeframes([]) == list(service.DEFAULT_TIMEFRAMES)


def test_normalize_timeframes_rejects_unknown():
    with pytest.raises(ValueError):
        service.normalize_timeframes(["1h", "7h"])


# --- capture ---

def test_capture_persists_row_with_task_id(shots_dir):
    tid = _make_task()
    _make_file(shots_dir, "BTCUSDT_1h_20260726_120000.png")
    with _chartshot(["BTCUSDT_1h_20260726_120000.png"]):
        out = service.capture("BINANCE:BTCUSDT.P", ["1h"], task_id=tid)

    assert out["ok"] is True
    assert out["symbol"] == "BTCUSDT"
    assert len(out["shots"]) == 1
    shot = out["shots"][0]
    assert shot["timeframe"] == "1h"
    assert shot["url"] == "/api/screenshots/file/BTCUSDT_1h_20260726_120000.png"

    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM screenshots WHERE id = ?", (shot["id"],)).fetchone()
    db.close()
    assert row["task_id"] == tid
    assert row["symbol"] == "BTCUSDT"


def test_capture_links_matching_signal(shots_dir):
    tid = _make_task()
    db = get_db(settings.db_path)
    db.execute("INSERT INTO signals (task_id, symbol, exchange, indicator, timeframe) "
               "VALUES (?, 'BTCUSDT', 'Binance', 'div', '1h')", (tid,))
    db.commit()
    sid = db.execute("SELECT id FROM signals").fetchone()["id"]
    db.close()

    _make_file(shots_dir, "BTCUSDT_1h_x.png")
    with _chartshot(["BTCUSDT_1h_x.png"]):
        out = service.capture("BTCUSDT", ["1h"], task_id=tid)

    assert service.get_shot(out["shots"][0]["id"])["signal_id"] == sid


def test_capture_without_signal_still_records_task_id(shots_dir):
    """匹配不到信号时 signal_id 为 NULL，但 task_id 必须在 —— 否则记录无法归属，
    任务删除时清理不掉（这正是旧实现的孤儿泄漏）。"""
    tid = _make_task()
    _make_file(shots_dir, "ETHUSDT_4h_x.png")
    with _chartshot(["ETHUSDT_4h_x.png"]):
        out = service.capture("ETHUSDT", ["4h"], task_id=tid)

    shot = service.get_shot(out["shots"][0]["id"])
    assert shot["signal_id"] is None
    assert shot["task_id"] == tid


def test_capture_surfaces_persist_failure_but_keeps_file(shots_dir):
    """悬空 task_id 触发外键失败：图已拍出来不能丢，但要报出「未落库」。"""
    _make_file(shots_dir, "BTCUSDT_1h_x.png")
    with _chartshot(["BTCUSDT_1h_x.png"]):
        out = service.capture("BTCUSDT", ["1h"], task_id=99999)

    assert len(out["shots"]) == 1
    assert out["shots"][0]["id"] is None
    assert any("未能落库" in e for e in out["errors"])
    assert service.list_shots() == []


def test_capture_partial_failure_keeps_successful_shots(shots_dir):
    """两个周期只回来一张图：成功的照常返回，缺的进 errors。"""
    _make_file(shots_dir, "BTCUSDT_1h_x.png")
    with _chartshot(["BTCUSDT_1h_x.png"]):
        out = service.capture("BTCUSDT", ["1h", "4h"])

    assert out["ok"] is True
    assert len(out["shots"]) == 1
    assert any("4h" in e for e in out["errors"])


def test_capture_flags_missing_file_on_disk(shots_dir):
    """ChartShot 报成功但文件没落到共享卷 —— 常见的 volume 挂载错配。"""
    with _chartshot(["BTCUSDT_1h_ghost.png"]):
        out = service.capture("BTCUSDT", ["1h"])

    assert out["ok"] is False
    assert out["shots"] == []
    assert any("不可读" in e for e in out["errors"])


def test_capture_returns_error_when_chartshot_fails(shots_dir):
    with patch("screenshots.client.chartshot_client.screenshot",
               return_value={"ok": False, "error": "Timeout"}):
        out = service.capture("BTCUSDT", ["1h"])

    assert out["ok"] is False
    assert out["errors"] == ["Timeout"]


def test_capture_rejects_empty_symbol(shots_dir):
    with pytest.raises(ValueError):
        service.capture("   ", ["1h"])


def test_capture_rejects_bad_timeframe(shots_dir):
    with pytest.raises(ValueError):
        service.capture("BTCUSDT", ["3y"])


# --- 查询 / 删除 ---

def test_list_shots_filters(shots_dir):
    tid = _make_task()
    for name, sym, tf in [("BTCUSDT_1h_a.png", "BTCUSDT", "1h"),
                          ("ETHUSDT_4h_b.png", "ETHUSDT", "4h")]:
        _make_file(shots_dir, name)
        with _chartshot([name]):
            service.capture(sym, [tf], task_id=tid)

    assert len(service.list_shots()) == 2
    assert len(service.list_shots(symbol="btcusdt")) == 1
    assert len(service.list_shots(timeframe="4h")) == 1
    assert len(service.list_shots(task_id=tid)) == 2
    assert len(service.list_shots(task_id=999)) == 0


def test_delete_shot_removes_row_and_file(shots_dir):
    path = _make_file(shots_dir, "BTCUSDT_1h_a.png")
    with _chartshot(["BTCUSDT_1h_a.png"]):
        out = service.capture("BTCUSDT", ["1h"])
    shot_id = out["shots"][0]["id"]

    assert service.delete_shot(shot_id) is True
    assert service.get_shot(shot_id) is None
    assert not os.path.exists(path)


def test_delete_shot_missing_returns_false(shots_dir):
    assert service.delete_shot(9999) is False


def test_delete_shot_tolerates_absent_file(shots_dir):
    """文件已被外部清掉时，记录仍应删干净。"""
    path = _make_file(shots_dir, "BTCUSDT_1h_a.png")
    with _chartshot(["BTCUSDT_1h_a.png"]):
        out = service.capture("BTCUSDT", ["1h"])
    os.remove(path)

    assert service.delete_shot(out["shots"][0]["id"]) is True


def test_get_screenshot_for_signal(shots_dir):
    db = get_db(settings.db_path)
    db.execute("INSERT INTO tasks (name, type) VALUES ('t', 'watchlist_signal')")
    db.execute("INSERT INTO signals (task_id, symbol, exchange, indicator, timeframe) "
               "VALUES (1, 'BTCUSDT', 'Binance', 'div', '1h')")
    db.commit()
    db.close()

    _make_file(shots_dir, "BTCUSDT_1h_x.png")
    with _chartshot(["BTCUSDT_1h_x.png"]):
        service.capture("BTCUSDT", ["1h"], task_id=1)

    assert service.get_screenshot_for_signal(1).endswith("BTCUSDT_1h_x.png")
    assert service.get_screenshot_for_signal(999) is None


# --- 定时任务优先（ChartShot 侧拒绝手动 job）---

def test_capture_marks_busy_when_rejected(shots_dir):
    """ChartShot 返回 busy 时要能和普通失败区分开。"""
    with patch("screenshots.client.chartshot_client.screenshot",
               return_value={"ok": False, "busy": True,
                             "error": "定时任务截图进行中，请稍后再试"}):
        out = service.capture("BTCUSDT", ["1h"])

    assert out["ok"] is False
    assert out["busy"] is True
    assert "定时任务" in out["errors"][0]


def test_capture_ordinary_failure_is_not_busy(shots_dir):
    with patch("screenshots.client.chartshot_client.screenshot",
               return_value={"ok": False, "error": "cookie 过期"}):
        out = service.capture("BTCUSDT", ["1h"])
    assert out["busy"] is False


def test_capture_defaults_to_manual_source(shots_dir):
    _make_file(shots_dir, "BTCUSDT_1h_x.png")
    with patch("screenshots.client.chartshot_client.screenshot",
               return_value={"ok": True, "files": ["BTCUSDT_1h_x.png"]}) as m:
        service.capture("BTCUSDT", ["1h"])
    assert m.call_args.kwargs["source"] == "manual"


def test_pipeline_capture_marks_source_as_task(shots_dir):
    """executor 走 pipeline，必须标记为 task 才能拿到队列优先权。"""
    from screenshots import pipeline
    _make_file(shots_dir, "BTCUSDT_1h_x.png")
    with patch("screenshots.client.chartshot_client.screenshot",
               return_value={"ok": True, "files": ["BTCUSDT_1h_x.png"]}) as m:
        pipeline.capture_and_dispatch(None, "BTCUSDT", ["1h"], channel=None)
    assert m.call_args.kwargs["source"] == "task"


# --- executor 截图数量上限与熔断 ---

def _cap_patch(results):
    """按顺序返回预设结果的 capture_and_dispatch 替身。"""
    calls = []

    def fake(task_id, symbol, timeframes, channel=None):
        calls.append(symbol)
        r = results[min(len(calls) - 1, len(results) - 1)]
        return {"shots": [{"id": 1}] if r else [], "errors": [], "pushes": []}

    return fake, calls


def test_capture_batch_respects_limit():
    from tasks import executor
    fake, calls = _cap_patch([True])
    with patch("tasks.executor.capture_and_dispatch", side_effect=fake):
        n = executor._capture_batch(1, [f"S{i}USDT" for i in range(182)],
                                    ["1h"], None, limit=3)
    assert len(calls) == 3      # 182 个候选只截 3 个
    assert n == 3


def test_capture_batch_breaks_on_failure_streak():
    """ChartShot 卡住时继续投递只会加深积压，连续失败即熔断。"""
    from tasks import executor
    fake, calls = _cap_patch([False])   # 每次都失败
    with patch("tasks.executor.capture_and_dispatch", side_effect=fake):
        n = executor._capture_batch(1, ["A", "B", "C", "D", "E"], ["1h"], None, limit=5)
    assert len(calls) == executor.SCREENSHOT_FAILURE_STREAK
    assert n == 0


def test_capture_batch_streak_resets_on_success():
    from tasks import executor
    seq = [False, True, False, False]
    fake, calls = _cap_patch(seq)
    with patch("tasks.executor.capture_and_dispatch", side_effect=fake):
        executor._capture_batch(1, ["A", "B", "C", "D"], ["1h"], None, limit=4)
    assert len(calls) == 4      # 中间成功一次，计数归零，没有提前熔断


def test_shot_limit_defaults_and_caps():
    from tasks import executor
    assert executor._shot_limit({}) == executor.DEFAULT_MAX_SCREENSHOTS
    assert executor._shot_limit({"max_screenshots": 5}) == 5
    assert executor._shot_limit({"max_screenshots": 999}) == executor.SCREENSHOT_HARD_CAP
    assert executor._shot_limit({"max_screenshots": 0}) == 0
    assert executor._shot_limit({"max_screenshots": "bad"}) == executor.DEFAULT_MAX_SCREENSHOTS


def test_capture_batch_zero_limit_captures_nothing():
    from tasks import executor
    fake, calls = _cap_patch([True])
    with patch("tasks.executor.capture_and_dispatch", side_effect=fake):
        executor._capture_batch(1, ["A", "B"], ["1h"], None, limit=0)
    assert calls == []

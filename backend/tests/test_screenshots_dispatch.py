import json
from unittest.mock import patch

import pytest

from config import settings
from database import get_db
from screenshots import dispatch


def _make_channel(type_="telegram", name="tg", config=None):
    cfg = config or {"bot_token": "t", "chat_id": "c"}
    db = get_db(settings.db_path)
    cur = db.execute("INSERT INTO channels (type, name, config_json) VALUES (?, ?, ?)",
                     (type_, name, json.dumps(cfg)))
    db.commit()
    cid = cur.lastrowid
    db.close()
    return cid


def _shot(symbol="BTCUSDT", tf="1h", name="BTCUSDT_1h_a.png"):
    return {"id": 1, "task_id": None, "symbol": symbol, "timeframe": tf,
            "filename": name, "file_path": f"/shots/{name}",
            "url": f"/api/screenshots/file/{name}"}


def _push_logs():
    db = get_db(settings.db_path)
    rows = db.execute("SELECT * FROM push_logs ORDER BY id").fetchall()
    db.close()
    return rows


# --- 渠道解析 ---

def test_resolve_channels_returns_telegram_and_discord():
    tg = _make_channel("telegram", "TG")
    dc = _make_channel("discord", "DC", {"bot_token": "b", "channel_id": "9"})

    channels, missing = dispatch.resolve_channels([tg, dc])

    assert [c["type"] for c in channels] == ["telegram", "discord"]
    assert channels[0]["config"]["bot_token"] == "t"
    assert missing == []


def test_resolve_channels_preserves_caller_order():
    tg = _make_channel("telegram", "TG")
    dc = _make_channel("discord", "DC", {"bot_token": "b", "channel_id": "9"})

    channels, _ = dispatch.resolve_channels([dc, tg])
    assert [c["id"] for c in channels] == [dc, tg]


def test_resolve_channels_skips_webhook_type():
    """webhook 没有图片上传语义，算作不可用渠道。"""
    wh = _make_channel("webhook", "WH", {"url": "https://x"})

    channels, missing = dispatch.resolve_channels([wh])
    assert channels == []
    assert missing == [wh]


def test_resolve_channels_reports_unknown_ids():
    tg = _make_channel()
    channels, missing = dispatch.resolve_channels([tg, 4242])
    assert len(channels) == 1
    assert missing == [4242]


def test_resolve_channels_empty_input():
    assert dispatch.resolve_channels([]) == ([], [])
    assert dispatch.resolve_channels(None) == ([], [])


# --- 推送 ---

def test_push_shots_sends_every_shot_to_every_channel():
    tg = _make_channel("telegram", "TG")
    dc = _make_channel("discord", "DC", {"bot_token": "b", "channel_id": "9"})
    channels, _ = dispatch.resolve_channels([tg, dc])
    shots = [_shot(tf="1h"), _shot(tf="4h", name="BTCUSDT_4h_b.png")]

    with patch("screenshots.dispatch.send_photo", return_value=101) as sp:
        results = dispatch.push_shots(shots, channels)

    assert sp.call_count == 4  # 2 图 × 2 渠道
    assert all(r["ok"] for r in results)
    assert [r["sent"] for r in results] == [2, 2]
    assert [r["type"] for r in results] == ["telegram", "discord"]


def test_push_shots_default_caption_has_symbol_and_timeframe():
    tg = _make_channel()
    channels, _ = dispatch.resolve_channels([tg])

    with patch("screenshots.dispatch.send_photo", return_value=1) as sp:
        dispatch.push_shots([_shot()], channels)

    assert sp.call_args.kwargs["caption"] == "📸 BTCUSDT 1h"


def test_push_shots_caption_template_substitution():
    tg = _make_channel()
    channels, _ = dispatch.resolve_channels([tg])

    with patch("screenshots.dispatch.send_photo", return_value=1) as sp:
        dispatch.push_shots([_shot()], channels, caption="🔔 {symbol} · {timeframe} 触发")

    assert sp.call_args.kwargs["caption"] == "🔔 BTCUSDT · 1h 触发"


def test_push_shots_caption_with_bare_braces_does_not_crash():
    """用户 caption 里的裸花括号不该被当成格式化占位符。"""
    tg = _make_channel()
    channels, _ = dispatch.resolve_channels([tg])

    with patch("screenshots.dispatch.send_photo", return_value=1) as sp:
        dispatch.push_shots([_shot()], channels, caption="{unknown} {symbol}")

    assert sp.call_args.kwargs["caption"] == "{unknown} BTCUSDT"


def test_push_shots_isolates_failure_per_channel():
    """telegram 挂掉不该影响 discord。"""
    tg = _make_channel("telegram", "TG")
    dc = _make_channel("discord", "DC", {"bot_token": "b", "channel_id": "9"})
    channels, _ = dispatch.resolve_channels([tg, dc])

    def fake(ch_type, config, path, caption=""):
        if ch_type == "telegram":
            raise RuntimeError("Telegram sendPhoto failed: chat not found")
        return 7

    with patch("screenshots.dispatch.send_photo", side_effect=fake):
        results = dispatch.push_shots([_shot()], channels)

    tg_res = next(r for r in results if r["type"] == "telegram")
    dc_res = next(r for r in results if r["type"] == "discord")
    assert tg_res["ok"] is False and tg_res["failed"] == 1
    assert dc_res["ok"] is True and dc_res["sent"] == 1


def test_push_shots_isolates_failure_per_shot():
    """同一渠道内，一张图失败不影响其余图。"""
    tg = _make_channel()
    channels, _ = dispatch.resolve_channels([tg])
    shots = [_shot(tf="1h"), _shot(tf="4h", name="b.png")]

    def fake(ch_type, config, path, caption=""):
        if "4h" in caption:
            raise RuntimeError("boom")
        return 1

    with patch("screenshots.dispatch.send_photo", side_effect=fake):
        results = dispatch.push_shots(shots, channels)

    assert results[0]["sent"] == 1
    assert results[0]["failed"] == 1
    assert results[0]["ok"] is False


def test_push_shots_writes_push_log_per_channel():
    tg = _make_channel("telegram", "TG")
    dc = _make_channel("discord", "DC", {"bot_token": "b", "channel_id": "9"})
    channels, _ = dispatch.resolve_channels([tg, dc])

    with patch("screenshots.dispatch.send_photo", return_value=1):
        dispatch.push_shots([_shot()], channels)

    logs = _push_logs()
    assert len(logs) == 2
    assert {l["channel_id"] for l in logs} == {tg, dc}
    assert all(l["status"] == "success" for l in logs)
    assert json.loads(logs[0]["image_paths"]) == ["BTCUSDT_1h_a.png"]


def test_push_log_records_failure_detail():
    tg = _make_channel()
    channels, _ = dispatch.resolve_channels([tg])

    with patch("screenshots.dispatch.send_photo", side_effect=RuntimeError("chat not found")):
        dispatch.push_shots([_shot()], channels)

    log = _push_logs()[0]
    assert log["status"] == "failed"
    assert "chat not found" in log["error_message"]


def test_push_shots_noop_without_shots_or_channels():
    tg = _make_channel()
    channels, _ = dispatch.resolve_channels([tg])
    with patch("screenshots.dispatch.send_photo") as sp:
        assert dispatch.push_shots([], channels) == []
        assert dispatch.push_shots([_shot()], []) == []
    sp.assert_not_called()
    assert _push_logs() == []


# --- 组合入口 ---

def test_capture_and_push_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "screenshots_dir", str(tmp_path))
    (tmp_path / "BTCUSDT_1h_x.png").write_bytes(b"\x89PNG")
    tg = _make_channel()

    with patch("screenshots.client.chartshot_client.screenshot",
               return_value={"ok": True, "files": ["BTCUSDT_1h_x.png"]}), \
         patch("screenshots.dispatch.send_photo", return_value=5) as sp:
        out = dispatch.capture_and_push("BINANCE:BTCUSDT.P", ["1h"], channel_ids=[tg])

    assert out["ok"] is True
    assert len(out["shots"]) == 1
    assert out["pushes"][0]["ok"] is True
    assert out["missing_channels"] == []
    assert sp.call_count == 1


def test_capture_and_push_skips_push_when_capture_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "screenshots_dir", str(tmp_path))
    tg = _make_channel()

    with patch("screenshots.client.chartshot_client.screenshot",
               return_value={"ok": False, "error": "Timeout"}), \
         patch("screenshots.dispatch.send_photo") as sp:
        out = dispatch.capture_and_push("BTCUSDT", ["1h"], channel_ids=[tg])

    assert out["ok"] is False
    assert out["pushes"] == []
    sp.assert_not_called()


def test_capture_and_push_reports_missing_channel(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "screenshots_dir", str(tmp_path))
    (tmp_path / "BTCUSDT_1h_x.png").write_bytes(b"\x89PNG")

    with patch("screenshots.client.chartshot_client.screenshot",
               return_value={"ok": True, "files": ["BTCUSDT_1h_x.png"]}):
        out = dispatch.capture_and_push("BTCUSDT", ["1h"], channel_ids=[777])

    assert out["ok"] is True
    assert out["missing_channels"] == [777]
    assert out["pushes"] == []

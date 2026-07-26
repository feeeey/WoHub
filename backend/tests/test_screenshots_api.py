import json
from unittest.mock import patch

import pytest

from config import settings
from database import get_db


@pytest.fixture
def shots_dir(tmp_path, monkeypatch):
    d = tmp_path / "shots"
    d.mkdir()
    monkeypatch.setattr(settings, "screenshots_dir", str(d))
    return d


def _chartshot(files):
    return patch("screenshots.client.chartshot_client.screenshot",
                 return_value={"ok": True, "files": files})


def _make_channel(type_="telegram", name="tg", config=None):
    cfg = config or {"bot_token": "t", "chat_id": "c"}
    db = get_db(settings.db_path)
    cur = db.execute("INSERT INTO channels (type, name, config_json) VALUES (?, ?, ?)",
                     (type_, name, json.dumps(cfg)))
    db.commit()
    cid = cur.lastrowid
    db.close()
    return cid


def _make_task():
    db = get_db(settings.db_path)
    cur = db.execute("INSERT INTO tasks (name, type) VALUES ('t', 'scheduled_shot')")
    db.commit()
    tid = cur.lastrowid
    db.close()
    return tid


async def _capture(client, shots_dir, filename="BTCUSDT_1h_a.png", **body):
    (shots_dir / filename).write_bytes(b"\x89PNG fake")
    payload = {"symbol": "BTCUSDT", "timeframes": ["1h"], **body}
    with _chartshot([filename]):
        return await client.post("/api/screenshots/capture", json=payload)


# --- 元信息 ---

@pytest.mark.asyncio
async def test_list_timeframes(client):
    resp = await client.get("/api/screenshots/timeframes")
    assert resp.status_code == 200
    data = resp.json()
    assert "1h" in data["timeframes"]
    assert data["default"] == ["1h"]


# --- capture ---

@pytest.mark.asyncio
async def test_capture_returns_shots(client, shots_dir):
    resp = await _capture(client, shots_dir)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["symbol"] == "BTCUSDT"
    assert data["shots"][0]["url"] == "/api/screenshots/file/BTCUSDT_1h_a.png"
    assert data["pushes"] == []


@pytest.mark.asyncio
async def test_capture_normalizes_pine_symbol(client, shots_dir):
    resp = await _capture(client, shots_dir, symbol="BINANCE:BTCUSDT.P")
    assert resp.json()["symbol"] == "BTCUSDT"


@pytest.mark.asyncio
async def test_capture_defaults_timeframes(client, shots_dir):
    (shots_dir / "BTCUSDT_1h_a.png").write_bytes(b"\x89PNG")
    with _chartshot(["BTCUSDT_1h_a.png"]):
        resp = await client.post("/api/screenshots/capture", json={"symbol": "BTCUSDT"})
    assert resp.json()["timeframes"] == ["1h"]


@pytest.mark.asyncio
async def test_capture_rejects_bad_timeframe(client, shots_dir):
    resp = await client.post("/api/screenshots/capture",
                             json={"symbol": "BTCUSDT", "timeframes": ["7h"]})
    assert resp.status_code == 400
    assert "7h" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_capture_rejects_empty_symbol(client, shots_dir):
    resp = await client.post("/api/screenshots/capture", json={"symbol": "  "})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_capture_rejects_unknown_task_id(client, shots_dir):
    """悬空 task_id 会在落库时触发外键失败，必须提前拦下。"""
    resp = await client.post("/api/screenshots/capture",
                             json={"symbol": "BTCUSDT", "task_id": 4242})
    assert resp.status_code == 400
    assert "4242" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_capture_accepts_real_task_id(client, shots_dir):
    tid = _make_task()
    resp = await _capture(client, shots_dir, task_id=tid)
    assert resp.json()["shots"][0]["task_id"] == tid


@pytest.mark.asyncio
async def test_capture_reports_chartshot_failure_as_200_with_ok_false(client, shots_dir):
    with patch("screenshots.client.chartshot_client.screenshot",
               return_value={"ok": False, "error": "Timeout"}):
        resp = await client.post("/api/screenshots/capture", json={"symbol": "BTCUSDT"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


@pytest.mark.asyncio
async def test_capture_pushes_to_both_channel_types(client, shots_dir):
    tg = _make_channel("telegram", "TG")
    dc = _make_channel("discord", "DC", {"bot_token": "b", "channel_id": "9"})

    with patch("screenshots.dispatch.send_photo", return_value=1) as sp:
        resp = await _capture(client, shots_dir, channel_ids=[tg, dc])

    data = resp.json()
    assert sp.call_count == 2
    assert [p["type"] for p in data["pushes"]] == ["telegram", "discord"]
    assert all(p["ok"] for p in data["pushes"])


@pytest.mark.asyncio
async def test_capture_partial_push_failure_still_returns_200(client, shots_dir):
    tg = _make_channel("telegram", "TG")
    dc = _make_channel("discord", "DC", {"bot_token": "b", "channel_id": "9"})

    def fake(ch_type, config, path, caption=""):
        if ch_type == "telegram":
            raise RuntimeError("chat not found")
        return 1

    with patch("screenshots.dispatch.send_photo", side_effect=fake):
        resp = await _capture(client, shots_dir, channel_ids=[tg, dc])

    pushes = resp.json()["pushes"]
    assert resp.status_code == 200
    assert next(p for p in pushes if p["type"] == "telegram")["ok"] is False
    assert next(p for p in pushes if p["type"] == "discord")["ok"] is True


# --- 列表 ---

@pytest.mark.asyncio
async def test_list_screenshots(client, shots_dir):
    await _capture(client, shots_dir, filename="BTCUSDT_1h_a.png")
    await _capture(client, shots_dir, filename="ETHUSDT_4h_b.png",
                   symbol="ETHUSDT", timeframes=["4h"])

    resp = await client.get("/api/screenshots")
    assert resp.status_code == 200
    assert len(resp.json()["screenshots"]) == 2

    resp = await client.get("/api/screenshots?symbol=ETHUSDT")
    rows = resp.json()["screenshots"]
    assert len(rows) == 1 and rows[0]["symbol"] == "ETHUSDT"


@pytest.mark.asyncio
async def test_list_screenshots_empty(client, shots_dir):
    resp = await client.get("/api/screenshots")
    assert resp.json()["screenshots"] == []


# --- 取图 ---

@pytest.mark.asyncio
async def test_get_file_serves_png(client, shots_dir):
    (shots_dir / "BTCUSDT_1h_a.png").write_bytes(b"\x89PNG fake")
    resp = await client.get("/api/screenshots/file/BTCUSDT_1h_a.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == b"\x89PNG fake"


@pytest.mark.asyncio
async def test_get_file_missing(client, shots_dir):
    resp = await client.get("/api/screenshots/file/nope.png")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_file_rejects_illegal_name(client, shots_dir):
    """文件名白名单：带路径分隔符或空格的名字直接拒掉。"""
    resp = await client.get("/api/screenshots/file/bad%20name.png")
    assert resp.status_code == 400


# --- 重推 ---

@pytest.mark.asyncio
async def test_push_existing_screenshot(client, shots_dir):
    cap = await _capture(client, shots_dir)
    shot_id = cap.json()["shots"][0]["id"]
    tg = _make_channel()

    with patch("screenshots.dispatch.send_photo", return_value=1) as sp:
        resp = await client.post(f"/api/screenshots/{shot_id}/push",
                                 json={"channel_ids": [tg], "caption": "复盘 {symbol}"})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert sp.call_args.kwargs["caption"] == "复盘 BTCUSDT"


@pytest.mark.asyncio
async def test_push_unknown_screenshot(client, shots_dir):
    tg = _make_channel()
    resp = await client.post("/api/screenshots/9999/push", json={"channel_ids": [tg]})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_push_rejects_when_no_usable_channel(client, shots_dir):
    cap = await _capture(client, shots_dir)
    shot_id = cap.json()["shots"][0]["id"]
    resp = await client.post(f"/api/screenshots/{shot_id}/push", json={"channel_ids": [777]})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_push_gone_when_file_deleted(client, shots_dir):
    cap = await _capture(client, shots_dir)
    shot_id = cap.json()["shots"][0]["id"]
    (shots_dir / "BTCUSDT_1h_a.png").unlink()
    tg = _make_channel()

    resp = await client.post(f"/api/screenshots/{shot_id}/push", json={"channel_ids": [tg]})
    assert resp.status_code == 410


# --- 删除 ---

@pytest.mark.asyncio
async def test_delete_screenshot(client, shots_dir):
    cap = await _capture(client, shots_dir)
    shot_id = cap.json()["shots"][0]["id"]

    resp = await client.delete(f"/api/screenshots/{shot_id}")
    assert resp.status_code == 200
    assert not (shots_dir / "BTCUSDT_1h_a.png").exists()
    assert (await client.get("/api/screenshots")).json()["screenshots"] == []


@pytest.mark.asyncio
async def test_delete_unknown_screenshot(client, shots_dir):
    resp = await client.delete("/api/screenshots/9999")
    assert resp.status_code == 404


# --- 任务级联清理 ---

@pytest.mark.asyncio
async def test_deleting_task_removes_orphan_screenshots(client, shots_dir):
    """截图匹配不到信号时 signal_id 为 NULL。旧实现只按 signal_id 级联删除，
    这类行永远清不掉；现在按 task_id 也能清。"""
    tid = _make_task()
    await _capture(client, shots_dir, task_id=tid)

    rows = (await client.get("/api/screenshots")).json()["screenshots"]
    assert len(rows) == 1 and rows[0]["signal_id"] is None

    assert (await client.delete(f"/api/tasks/{tid}")).status_code == 200
    assert (await client.get("/api/screenshots")).json()["screenshots"] == []


# --- 鉴权 ---

@pytest.mark.asyncio
@pytest.mark.no_auth_override
async def test_screenshot_endpoints_require_auth(client, shots_dir):
    for method, url in [("GET", "/api/screenshots"),
                        ("POST", "/api/screenshots/capture"),
                        ("GET", "/api/screenshots/file/a.png"),
                        ("DELETE", "/api/screenshots/1")]:
        resp = await client.request(method, url, json={"symbol": "BTCUSDT"})
        assert resp.status_code == 401, f"{method} {url} 未受鉴权保护"


# --- 定时任务优先 ---

@pytest.mark.asyncio
async def test_capture_returns_409_when_task_capture_running(client, shots_dir):
    """手动截图撞上定时任务时返回 409，而不是含糊的 200+ok:false。"""
    with patch("screenshots.client.chartshot_client.screenshot",
               return_value={"ok": False, "busy": True,
                             "error": "定时任务截图进行中，请稍后再试"}):
        resp = await client.post("/api/screenshots/capture",
                                 json={"symbol": "BTCUSDT", "timeframes": ["1h"]})

    assert resp.status_code == 409
    assert "定时任务" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_capture_ordinary_failure_still_200(client, shots_dir):
    """普通失败仍是 200+ok:false，不能和 busy 混为一谈。"""
    with patch("screenshots.client.chartshot_client.screenshot",
               return_value={"ok": False, "error": "cookie 过期"}):
        resp = await client.post("/api/screenshots/capture", json={"symbol": "BTCUSDT"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


@pytest.mark.asyncio
async def test_manual_capture_sends_manual_source(client, shots_dir):
    (shots_dir / "BTCUSDT_1h_a.png").write_bytes(b"\x89PNG")
    with patch("screenshots.client.chartshot_client.screenshot",
               return_value={"ok": True, "files": ["BTCUSDT_1h_a.png"]}) as m:
        await client.post("/api/screenshots/capture", json={"symbol": "BTCUSDT"})
    assert m.call_args.kwargs["source"] == "manual"

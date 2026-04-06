# WoHub Phase 1e: Task Engine Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the task engine — APScheduler-based scheduler, task executor framework, Pine screener data source migration, task CRUD API, and task management frontend. This is the heart of the platform.

**Architecture:** Tasks are stored in the SQLite `tasks` table. APScheduler runs cron jobs per task. The executor loads task config, calls the appropriate data source, evaluates triggers, executes actions (text push, screenshot, etc.), and logs results. Pine screener configs (JSON files) are migrated from the existing project.

**Tech Stack:** Python 3.13 / FastAPI / APScheduler / requests / SQLite / Vue 3

---

## File Map

### Backend — New Files

| File | Responsibility |
|------|---------------|
| `backend/sources/pine_screener.py` | TradingView Pine Scanner API client + cross-analysis |
| `backend/tasks/__init__.py` | Empty package init |
| `backend/tasks/scheduler.py` | APScheduler setup + task job management |
| `backend/tasks/executor.py` | Task execution orchestration |
| `backend/api/tasks.py` | Task CRUD + start/stop/test API routes |
| `backend/tests/test_pine_screener.py` | Pine screener tests (mocked) |
| `backend/tests/test_task_api.py` | Task API tests |
| `backend/screeners/` | Migrated screener JSON configs |

### Backend — Modified Files

| File | Change |
|------|--------|
| `backend/pyproject.toml` | Add `apscheduler` dependency |
| `backend/api/__init__.py` | Register tasks router |
| `backend/main.py` | Start/stop scheduler in lifespan |

### Frontend — Modified Files

| File | Change |
|------|--------|
| `frontend/src/views/Tasks.vue` | Task management UI |
| `frontend/src/api/client.js` | Add task API methods |

---

### Task 1: Pine Screener Data Source

**Files:**
- Modify: `backend/pyproject.toml` (add apscheduler)
- Create: `backend/sources/pine_screener.py`
- Create: `backend/tests/test_pine_screener.py`
- Copy: screener JSON configs to `backend/screeners/`

- [ ] **Step 1: Add apscheduler to dependencies**

Add `"apscheduler>=3.10.0,<4.0"` to dependencies in `backend/pyproject.toml`. Install: `pip install -e ".[dev]"`

- [ ] **Step 2: Copy screener JSON configs**

```bash
mkdir -p backend/screeners/oscillator backend/screeners/trend
cp pine-screener/oscillator_screener/*.json backend/screeners/oscillator/
cp pine-screener/trend_screener/*.json backend/screeners/trend/
```

These are the TradingView Pine indicator definitions. They don't change — just static config files.

- [ ] **Step 3: Write tests**

Create `backend/tests/test_pine_screener.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from sources.pine_screener import (
    list_screeners,
    run_screener,
    build_cross_analysis,
    fetch_watchlists,
)


def test_list_screeners():
    screeners = list_screeners()
    assert len(screeners) > 0
    for s in screeners:
        assert "folder_type" in s
        assert "screener_name" in s
        assert "label" in s
        assert s["folder_type"] in ("oscillator", "trend")


def test_list_screeners_has_divergence():
    screeners = list_screeners()
    names = [s["screener_name"] for s in screeners]
    assert "divergence" in names


MOCK_RESPONSE_TEXT = '{"snapshot":{"symbols":[{"s":"BINANCE:BTCUSDT.P"},{"s":"BINANCE:ETHUSDT.P"}]}}\n'


def test_run_screener_parses_response():
    mock_resp = MagicMock()
    mock_resp.text = MOCK_RESPONSE_TEXT
    mock_resp.raise_for_status = MagicMock()

    with patch("sources.pine_screener._get_session") as mock_sess:
        mock_sess.return_value.post.return_value = mock_resp
        symbols = run_screener("oscillator", "divergence", "1h", 12345)
        assert "BINANCE:BTCUSDT.P" in symbols
        assert "BINANCE:ETHUSDT.P" in symbols


def test_run_screener_invalid_folder():
    with pytest.raises(ValueError, match="folder_type"):
        run_screener("invalid", "divergence", "1h", 12345)


def test_run_screener_invalid_resolution():
    with pytest.raises(ValueError, match="resolution"):
        run_screener("oscillator", "divergence", "99h", 12345)


def test_cross_analysis_screener_overlap():
    results = [
        {"label": "A", "resolution": "1h", "symbols": ["BTC", "ETH", "SOL"]},
        {"label": "B", "resolution": "1h", "symbols": ["BTC", "SOL", "DOGE"]},
        {"label": "C", "resolution": "1h", "symbols": ["BTC"]},
    ]
    analysis = build_cross_analysis(results)
    # BTC appears in all 3 screeners
    assert "BTC" in analysis["screener_overlap"]
    assert len(analysis["screener_overlap"]["BTC"]) == 3
    # SOL appears in 2
    assert "SOL" in analysis["screener_overlap"]


def test_cross_analysis_full_overlap():
    results = [
        {"label": "A", "resolution": "1h", "symbols": ["BTC", "ETH"]},
        {"label": "B", "resolution": "1h", "symbols": ["BTC", "SOL"]},
    ]
    analysis = build_cross_analysis(results)
    assert "BTC" in analysis["full_overlap"]
    assert "ETH" not in analysis["full_overlap"]
```

- [ ] **Step 4: Implement pine_screener.py**

Create `backend/sources/pine_screener.py`:

```python
import os
import json
import requests
from pathlib import Path
from config import settings

SCREENERS_DIR = Path(__file__).resolve().parent.parent / "screeners"

SCREENER_NAMES = {
    "oscillator/divergence": "顶底背离",
    "oscillator/overbought_zone": "超买",
    "oscillator/oversold_zone": "超卖",
    "oscillator/volatility_alert": "波动警报",
    "trend/shadows": "长上影/长下影",
    "trend/trend_volume_spike": "趋势爆量",
}

RESOLUTION_MAP = {
    "1m": "1", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "4h": "240", "1d": "1D", "1w": "1W",
}

VALID_RESOLUTIONS = set(RESOLUTION_MAP.keys())

API_URL = "https://pine-screener.tradingview.com/pine_scanner_http/scan"

_HEADERS = {
    "accept": "application/json",
    "content-type": "text/plain;charset=UTF-8",
    "origin": "https://www.tradingview.com",
    "referer": "https://www.tradingview.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

_session = None


def _get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(_HEADERS)
        _session.timeout = 15
        if settings.proxy_enabled:
            proxy = f"http://{settings.proxy_host}:{settings.proxy_port}"
            _session.proxies = {"http": proxy, "https": proxy}
    return _session


def _load_cookies():
    cookie_path = Path(settings.db_path).parent / "cookies.json"
    if cookie_path.exists():
        with open(cookie_path) as f:
            return json.load(f)
    return {}


def list_screeners():
    result = []
    for folder in ("oscillator", "trend"):
        folder_path = SCREENERS_DIR / folder
        if not folder_path.exists():
            continue
        for f in sorted(folder_path.glob("*.json")):
            name = f.stem
            key = f"{folder}/{name}"
            result.append({
                "folder_type": folder,
                "screener_name": name,
                "label": SCREENER_NAMES.get(key, name),
            })
    return result


def run_screener(folder_type, screener_name, resolution, watchlist_id):
    if folder_type not in ("oscillator", "trend"):
        raise ValueError(f"Invalid folder_type: {folder_type}")
    if resolution not in VALID_RESOLUTIONS:
        raise ValueError(f"Invalid resolution: {resolution}")

    config_path = SCREENERS_DIR / folder_type / f"{screener_name}.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Screener not found: {config_path}")

    with open(config_path) as f:
        config = json.load(f)

    config["scripts"][0]["resolution"] = RESOLUTION_MAP[resolution]
    config["watchlist"] = watchlist_id

    session = _get_session()
    cookies = _load_cookies()
    resp = session.post(API_URL, data=json.dumps(config), cookies=cookies)
    resp.raise_for_status()

    symbols = []
    seen = set()
    for line in resp.text.strip().split("\n"):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            for sym in data.get("snapshot", {}).get("symbols", []):
                s = sym.get("s", "")
                if s and s not in seen:
                    seen.add(s)
                    symbols.append(s)
        except json.JSONDecodeError:
            continue
    return symbols


def fetch_watchlists():
    session = _get_session()
    cookies = _load_cookies()
    headers = {
        "accept": "*/*",
        "referer": "https://www.tradingview.com/pine-screener/",
        "x-language": "zh_CN",
        "x-requested-with": "XMLHttpRequest",
    }
    resp = session.get(
        "https://www.tradingview.com/api/v1/symbols_list/all/",
        headers=headers,
        cookies=cookies,
    )
    resp.raise_for_status()
    return {item["name"]: item["id"] for item in resp.json() if item.get("name")}


def build_cross_analysis(results):
    # Screener overlap: symbols appearing in >=2 screeners
    symbol_screeners = {}
    for r in results:
        for sym in r.get("symbols", []):
            symbol_screeners.setdefault(sym, []).append(r["label"])
    screener_overlap = {
        sym: labels for sym, labels in symbol_screeners.items() if len(labels) >= 2
    }

    # Resolution overlap: per screener, symbols in >=2 resolutions
    screener_res = {}
    for r in results:
        label = r["label"]
        res = r.get("resolution", "")
        for sym in r.get("symbols", []):
            screener_res.setdefault(label, {}).setdefault(sym, []).append(res)
    resolution_overlap = {}
    for label, syms in screener_res.items():
        multi = {sym: ress for sym, ress in syms.items() if len(ress) >= 2}
        if multi:
            resolution_overlap[label] = multi

    # Full overlap: intersection of ALL result sets
    if results:
        sets = [set(r.get("symbols", [])) for r in results if r.get("symbols")]
        full = set.intersection(*sets) if sets else set()
    else:
        full = set()

    return {
        "screener_overlap": screener_overlap,
        "screener_overlap_count": len(screener_overlap),
        "resolution_overlap": resolution_overlap,
        "full_overlap": sorted(full),
        "full_overlap_count": len(full),
    }
```

- [ ] **Step 5: Run tests**

```bash
cd backend && source .venv/Scripts/activate && python -m pytest tests/test_pine_screener.py -v
```

Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/sources/pine_screener.py backend/tests/test_pine_screener.py backend/screeners/
git commit -m "feat: add Pine screener data source with cross-analysis"
```

---

### Task 2: Task Scheduler

**Files:**
- Create: `backend/tasks/__init__.py`
- Create: `backend/tasks/scheduler.py`

- [ ] **Step 1: Create scheduler module**

Create `backend/tasks/__init__.py` (empty file).

Create `backend/tasks/scheduler.py`:

```python
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

_scheduler = None

CRON_TRIGGERS = {
    "5m": CronTrigger(minute="3,8,13,18,23,28,33,38,43,48,53,58", timezone="UTC"),
    "15m": CronTrigger(minute="13,28,43,58", timezone="UTC"),
    "30m": CronTrigger(minute="28,58", timezone="UTC"),
    "1h": CronTrigger(minute=58, timezone="UTC"),
    "4h": CronTrigger(hour="3,7,11,15,19,23", minute=58, timezone="UTC"),
    "1d": CronTrigger(hour=23, minute=58, timezone="UTC"),
    "1w": CronTrigger(day_of_week="sun", hour=23, minute=58, timezone="UTC"),
}

SCHEDULE_DESC = {
    "5m": "每5分钟",
    "15m": "每15分钟",
    "30m": "每30分钟",
    "1h": "每小时 :58 UTC",
    "4h": "每4小时",
    "1d": "每天 23:58 UTC",
    "1w": "每周日 23:58 UTC",
}

RESOLUTION_PRIORITY = ["5m", "15m", "30m", "1h", "4h", "1d", "1w"]


def get_scheduler():
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone="UTC")
        _scheduler.start()
    return _scheduler


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def add_task_job(task_id, func, schedule_key):
    scheduler = get_scheduler()
    job_id = f"task_{task_id}"

    # Remove existing job if any
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass

    trigger = CRON_TRIGGERS.get(schedule_key)
    if trigger:
        scheduler.add_job(func, trigger, id=job_id, args=[task_id], replace_existing=True)
    else:
        # Fallback: interval in seconds
        try:
            seconds = int(schedule_key)
            scheduler.add_job(func, IntervalTrigger(seconds=seconds), id=job_id, args=[task_id], replace_existing=True)
        except ValueError:
            raise ValueError(f"Invalid schedule: {schedule_key}")


def remove_task_job(task_id):
    scheduler = get_scheduler()
    job_id = f"task_{task_id}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass


def is_task_running(task_id):
    scheduler = get_scheduler()
    job_id = f"task_{task_id}"
    return scheduler.get_job(job_id) is not None


def get_shortest_resolution(resolutions):
    for r in RESOLUTION_PRIORITY:
        if r in resolutions:
            return r
    return resolutions[0] if resolutions else "1h"
```

- [ ] **Step 2: Commit**

```bash
git add backend/tasks/__init__.py backend/tasks/scheduler.py
git commit -m "feat: add APScheduler-based task scheduler"
```

---

### Task 3: Task Executor

**Files:**
- Create: `backend/tasks/executor.py`

- [ ] **Step 1: Create executor**

Create `backend/tasks/executor.py`:

```python
import json
import traceback
from datetime import datetime, timezone
from database import get_db
from config import settings
from sources.pine_screener import run_screener, build_cross_analysis
from channels.sender import send_text, send_photo
from sources.chart_shot_client import chartshot_client


def execute_task(task_id):
    """Main task execution entry point. Called by scheduler."""
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        db.close()
        return

    task_type = row["type"]
    config = json.loads(row["config_json"])
    actions = json.loads(row["actions_json"])
    channel_id = row["channel_id"]

    # Get channel config if set
    channel = None
    if channel_id:
        ch_row = db.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
        if ch_row:
            channel = {
                "type": ch_row["type"],
                "config": json.loads(ch_row["config_json"]),
            }
    db.close()

    try:
        if task_type == "watchlist_signal":
            _exec_watchlist_signal(task_id, config, actions, channel)
        elif task_type == "market_scan":
            _exec_market_scan(task_id, config, actions, channel)
        elif task_type == "anomaly_watch":
            _exec_anomaly_watch(task_id, config, actions, channel)
        elif task_type == "scheduled_shot":
            _exec_scheduled_shot(task_id, config, actions, channel)
        else:
            print(f"[executor] Unknown task type: {task_type}")
    except Exception as e:
        print(f"[executor] Task {task_id} failed: {e}")
        traceback.print_exc()
        _log_push(task_id, channel, f"Task execution failed: {e}", status="failed", error=str(e))


def _exec_watchlist_signal(task_id, config, actions, channel):
    """Execute watchlist signal monitoring task."""
    watchlist_id = config.get("watchlist_id", 0)
    screeners = config.get("screeners", [])
    resolutions = config.get("resolutions", ["1h"])

    all_results = []
    for res in resolutions:
        for sc in screeners:
            try:
                symbols = run_screener(
                    sc["folder_type"], sc["screener_name"], res, watchlist_id
                )
                label = sc.get("label", sc["screener_name"])
                all_results.append({
                    "label": label,
                    "resolution": res,
                    "symbols": symbols,
                    "count": len(symbols),
                })
            except Exception as e:
                print(f"[executor] Screener error: {e}")

    if not all_results:
        return

    analysis = build_cross_analysis(all_results)
    overlaps = analysis.get("screener_overlap", {})

    if not overlaps:
        return

    # Build message
    lines = [f"🔔 信号触发 [{datetime.now(timezone.utc).strftime('%m-%d %H:%M')} UTC]"]
    for sym, labels in sorted(overlaps.items(), key=lambda x: -len(x[1])):
        clean_sym = sym.replace("BINANCE:", "").replace(".P", "")
        lines.append(f"  {clean_sym} → {' · '.join(labels)}")
    lines.append(f"\n共 {len(overlaps)} 个标的")
    message = "\n".join(lines)

    # Execute actions
    if "text_summary" in actions and channel:
        _send_push(channel, message)
        _log_push(task_id, channel, message)

    if "chart_shot" in actions and channel:
        for sym in list(overlaps.keys())[:3]:
            clean = sym.replace("BINANCE:", "").replace(".P", "")
            _take_and_send_screenshot(task_id, clean, resolutions, channel)

    # Record signals
    _record_signals(task_id, overlaps, resolutions, all_results)


def _exec_market_scan(task_id, config, actions, channel):
    """Execute full market scan task."""
    from sources.exchanges import fetch_all_tickers

    screeners = config.get("screeners", [])
    resolutions = config.get("resolutions", ["1h"])
    overlap_threshold = config.get("overlap_threshold", 2)
    watchlist_id = config.get("watchlist_id", 0)

    all_results = []
    for res in resolutions:
        for sc in screeners:
            try:
                symbols = run_screener(
                    sc["folder_type"], sc["screener_name"], res, watchlist_id
                )
                label = sc.get("label", sc["screener_name"])
                all_results.append({
                    "label": label, "resolution": res,
                    "symbols": symbols, "count": len(symbols),
                })
            except Exception as e:
                print(f"[executor] Screener error: {e}")

    analysis = build_cross_analysis(all_results)
    overlaps = {
        sym: labels
        for sym, labels in analysis.get("screener_overlap", {}).items()
        if len(labels) >= overlap_threshold
    }

    if not overlaps and not all_results:
        return

    # Summary message
    lines = [f"���� 全市场扫描 [{datetime.now(timezone.utc).strftime('%m-%d %H:%M')} UTC]"]
    for r in all_results:
        lines.append(f"  {r['label']} ({r['resolution']}): {r['count']} 命中")
    if overlaps:
        lines.append(f"\n🎯 {len(overlaps)} 个标的 ≥{overlap_threshold} 信号叠加:")
        for sym, labels in sorted(overlaps.items(), key=lambda x: -len(x[1])):
            clean = sym.replace("BINANCE:", "").replace(".P", "")
            lines.append(f"  {clean} ({len(labels)}): {' · '.join(labels)}")
    message = "\n".join(lines)

    if "text_summary" in actions and channel:
        _send_push(channel, message)
        _log_push(task_id, channel, message)

    if "chart_shot" in actions and channel and overlaps:
        shot_threshold = config.get("screenshot_threshold", 3)
        for sym in list(overlaps.keys()):
            if len(overlaps[sym]) >= shot_threshold:
                clean = sym.replace("BINANCE:", "").replace(".P", "")
                _take_and_send_screenshot(task_id, clean, resolutions[:1], channel)

    _record_signals(task_id, overlaps, resolutions, all_results)


def _exec_anomaly_watch(task_id, config, actions, channel):
    """Execute anomaly watch task."""
    from sources.exchanges import fetch_all_tickers, fetch_all_funding_rates

    monitor_type = config.get("monitor_type", "price_change")
    threshold = config.get("threshold", 10.0)
    screeners = config.get("screeners", [])
    resolutions = config.get("resolutions", ["1h"])
    watchlist_id = config.get("watchlist_id", 0)

    # Find anomalies
    anomalies = []
    if monitor_type == "price_change":
        tickers, _ = fetch_all_tickers()
        anomalies = [
            t for t in tickers
            if abs(t["priceChangePercent"]) >= threshold
            and t["volume24h"] >= settings.min_volume_24h
        ]
    elif monitor_type == "funding_rate":
        rates, _ = fetch_all_funding_rates()
        anomalies = [r for r in rates if abs(r["fundingRate"]) >= threshold / 10000]

    if not anomalies:
        return

    # Check if anomalies have Pine signals
    # Build a quick lookup of which symbols have screener hits
    signal_hits = {}
    for sc in screeners:
        for res in resolutions:
            try:
                symbols = run_screener(
                    sc["folder_type"], sc["screener_name"], res, watchlist_id
                )
                label = sc.get("label", sc["screener_name"])
                for sym in symbols:
                    signal_hits.setdefault(sym, []).append(label)
            except Exception:
                pass

    # Match anomalies with signal hits
    matches = []
    for a in anomalies:
        sym = a["symbol"]
        full_sym = f"BINANCE:{sym}.P"
        if full_sym in signal_hits:
            matches.append({**a, "signals": signal_hits[full_sym]})

    if not matches and "text_summary" not in actions:
        return

    lines = [f"⚠️ 异常行情监控 [{datetime.now(timezone.utc).strftime('%m-%d %H:%M')} UTC]"]
    lines.append(f"发现 {len(anomalies)} 个异常标的")
    if matches:
        lines.append(f"其中 {len(matches)} 个有信号配合:")
        for m in matches[:10]:
            lines.append(f"  {m['symbol']} ({m.get('priceChangePercent', 0):+.2f}%) → {' · '.join(m['signals'])}")
    message = "\n".join(lines)

    if "text_summary" in actions and channel:
        _send_push(channel, message)
        _log_push(task_id, channel, message)

    if "chart_shot" in actions and channel:
        for m in matches[:3]:
            _take_and_send_screenshot(task_id, m["symbol"], resolutions[:1], channel)


def _exec_scheduled_shot(task_id, config, actions, channel):
    """Execute scheduled screenshot task."""
    symbols = config.get("symbols", [])
    timeframes = config.get("timeframes", ["1h"])

    for sym in symbols:
        _take_and_send_screenshot(task_id, sym, timeframes, channel)


# ---- Helpers ----

def _send_push(channel, text):
    try:
        send_text(channel["type"], channel["config"], text)
    except Exception as e:
        print(f"[executor] Push failed: {e}")


def _take_and_send_screenshot(task_id, symbol, timeframes, channel):
    try:
        result = chartshot_client.screenshot(symbol, timeframes)
        if result.get("ok") and result.get("files"):
            for filename in result["files"]:
                photo_url = chartshot_client.screenshot_url(filename)
                # For now, send the URL as text since we can't send files across containers directly
                _send_push(channel, f"📸 {symbol} 截图: {photo_url}")
    except Exception as e:
        print(f"[executor] Screenshot failed for {symbol}: {e}")


def _log_push(task_id, channel, content, status="success", error=None):
    try:
        db = get_db(settings.db_path)
        db.execute(
            "INSERT INTO push_logs (task_id, channel_id, content_text, status, error_message) VALUES (?, ?, ?, ?, ?)",
            (task_id, channel.get("id") if channel else None, content[:1000], status, error),
        )
        db.commit()
        db.close()
    except Exception:
        pass


def _record_signals(task_id, overlaps, resolutions, all_results):
    """Record signals to the signals table for data accumulation."""
    try:
        db = get_db(settings.db_path)
        for sym, labels in overlaps.items():
            clean = sym.replace("BINANCE:", "").replace(".P", "")
            exchange = "Binance"
            if "OKX:" in sym:
                exchange = "OKX"
            elif "BYBIT:" in sym:
                exchange = "Bybit"

            for label in labels:
                for res in resolutions:
                    db.execute(
                        "INSERT INTO signals (task_id, symbol, exchange, indicator, timeframe) VALUES (?, ?, ?, ?, ?)",
                        (task_id, clean, exchange, label, res),
                    )
        db.commit()
        db.close()
    except Exception as e:
        print(f"[executor] Failed to record signals: {e}")
```

- [ ] **Step 2: Commit**

```bash
git add backend/tasks/executor.py
git commit -m "feat: add task executor with 4 task type handlers"
```

---

### Task 4: Task CRUD API

**Files:**
- Create: `backend/api/tasks.py`
- Modify: `backend/api/__init__.py`
- Modify: `backend/main.py` (scheduler lifecycle)
- Create: `backend/tests/test_task_api.py`

- [ ] **Step 1: Write tests**

Create `backend/tests/test_task_api.py`:

```python
import pytest
import json


@pytest.mark.asyncio
async def test_create_task(client):
    resp = await client.post("/api/tasks", json={
        "name": "Test Task",
        "type": "watchlist_signal",
        "config": {"watchlist_id": 123, "screeners": [], "resolutions": ["1h"]},
        "actions": ["text_summary"],
        "schedule": "1h",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] > 0
    assert data["name"] == "Test Task"
    assert data["type"] == "watchlist_signal"
    assert data["enabled"] is False  # default off


@pytest.mark.asyncio
async def test_list_tasks(client):
    await client.post("/api/tasks", json={
        "name": "T1", "type": "watchlist_signal",
        "config": {}, "actions": [], "schedule": "1h",
    })
    await client.post("/api/tasks", json={
        "name": "T2", "type": "market_scan",
        "config": {}, "actions": [], "schedule": "4h",
    })
    resp = await client.get("/api/tasks")
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


@pytest.mark.asyncio
async def test_update_task(client):
    create = await client.post("/api/tasks", json={
        "name": "Old", "type": "watchlist_signal",
        "config": {}, "actions": [], "schedule": "1h",
    })
    tid = create.json()["id"]
    resp = await client.put(f"/api/tasks/{tid}", json={"name": "New"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"


@pytest.mark.asyncio
async def test_delete_task(client):
    create = await client.post("/api/tasks", json={
        "name": "Del", "type": "watchlist_signal",
        "config": {}, "actions": [], "schedule": "1h",
    })
    tid = create.json()["id"]
    resp = await client.delete(f"/api/tasks/{tid}")
    assert resp.status_code == 200

    listing = await client.get("/api/tasks")
    ids = [t["id"] for t in listing.json()]
    assert tid not in ids


@pytest.mark.asyncio
async def test_get_screeners(client):
    resp = await client.get("/api/tasks/screeners")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0
    assert "folder_type" in data[0]
```

- [ ] **Step 2: Implement task API**

Create `backend/api/tasks.py`:

```python
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db
from config import settings
from tasks.scheduler import (
    add_task_job, remove_task_job, is_task_running,
    get_shortest_resolution, SCHEDULE_DESC,
)
from tasks.executor import execute_task
from sources.pine_screener import list_screeners

router = APIRouter(prefix="/tasks")

VALID_TYPES = {"watchlist_signal", "market_scan", "anomaly_watch", "scheduled_shot"}


class TaskCreate(BaseModel):
    name: str
    type: str
    config: dict
    actions: list
    schedule: str
    channel_id: Optional[int] = None


class TaskUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None
    actions: Optional[list] = None
    schedule: Optional[str] = None
    channel_id: Optional[int] = None
    enabled: Optional[bool] = None


def _row_to_dict(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "config": json.loads(row["config_json"]),
        "actions": json.loads(row["actions_json"]),
        "channel_id": row["channel_id"],
        "schedule": row["schedule"],
        "enabled": bool(row["enabled"]),
        "running": is_task_running(row["id"]),
        "schedule_desc": SCHEDULE_DESC.get(row["schedule"], row["schedule"]),
        "created_at": row["created_at"],
    }


@router.get("/screeners")
def get_screeners():
    return list_screeners()


@router.get("")
def list_tasks():
    db = get_db(settings.db_path)
    rows = db.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
    db.close()
    return [_row_to_dict(r) for r in rows]


@router.post("")
def create_task(body: TaskCreate):
    if body.type not in VALID_TYPES:
        raise HTTPException(400, f"Invalid type: {body.type}")

    db = get_db(settings.db_path)
    cursor = db.execute(
        "INSERT INTO tasks (name, type, config_json, actions_json, channel_id, schedule) VALUES (?, ?, ?, ?, ?, ?)",
        (body.name, body.type, json.dumps(body.config), json.dumps(body.actions),
         body.channel_id, body.schedule),
    )
    db.commit()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)).fetchone()
    db.close()
    return _row_to_dict(row)


@router.put("/{task_id}")
def update_task(task_id: int, body: TaskUpdate):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "Task not found")

    updates, params = [], []
    if body.name is not None:
        updates.append("name = ?"); params.append(body.name)
    if body.config is not None:
        updates.append("config_json = ?"); params.append(json.dumps(body.config))
    if body.actions is not None:
        updates.append("actions_json = ?"); params.append(json.dumps(body.actions))
    if body.schedule is not None:
        updates.append("schedule = ?"); params.append(body.schedule)
    if body.channel_id is not None:
        updates.append("channel_id = ?"); params.append(body.channel_id)
    if body.enabled is not None:
        updates.append("enabled = ?"); params.append(int(body.enabled))

    if updates:
        updates.append("updated_at = datetime('now')")
        params.append(task_id)
        db.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", params)
        db.commit()

    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()

    # Reschedule if running and schedule changed
    if body.enabled is not None or body.schedule is not None:
        if bool(row["enabled"]):
            _start_job(row)
        else:
            remove_task_job(task_id)

    return _row_to_dict(row)


@router.delete("/{task_id}")
def delete_task(task_id: int):
    remove_task_job(task_id)
    db = get_db(settings.db_path)
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    db.commit()
    db.close()
    return {"ok": True}


@router.post("/{task_id}/start")
def start_task(task_id: int):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "Task not found")
    db.execute("UPDATE tasks SET enabled = 1 WHERE id = ?", (task_id,))
    db.commit()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    _start_job(row)
    return _row_to_dict(row)


@router.post("/{task_id}/stop")
def stop_task(task_id: int):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "Task not found")
    db.execute("UPDATE tasks SET enabled = 0 WHERE id = ?", (task_id,))
    db.commit()
    remove_task_job(task_id)
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    return _row_to_dict(row)


@router.post("/{task_id}/test")
def test_task(task_id: int):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "Task not found")
    try:
        execute_task(task_id)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _start_job(row):
    schedule = row["schedule"]
    config = json.loads(row["config_json"])
    resolutions = config.get("resolutions", [schedule])
    if isinstance(resolutions, list) and len(resolutions) > 1:
        schedule = get_shortest_resolution(resolutions)
    add_task_job(row["id"], execute_task, schedule)


def start_all_enabled():
    """Start scheduler jobs for all enabled tasks. Called on app startup."""
    db = get_db(settings.db_path)
    rows = db.execute("SELECT * FROM tasks WHERE enabled = 1").fetchall()
    db.close()
    for row in rows:
        try:
            _start_job(row)
        except Exception as e:
            print(f"[scheduler] Failed to start task {row['id']}: {e}")
```

- [ ] **Step 3: Register tasks router in `backend/api/__init__.py`**

Add import and include:
```python
from api.tasks import router as tasks_router
api_router.include_router(tasks_router)
```

- [ ] **Step 4: Update `backend/main.py` lifespan for scheduler**

Add scheduler start/stop to the lifespan:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from config import settings
from database import init_db
from api import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(settings.db_path)
    # Start all enabled task schedules
    from api.tasks import start_all_enabled
    start_all_enabled()
    yield
    # Shutdown scheduler
    from tasks.scheduler import stop_scheduler
    stop_scheduler()
```

Keep the rest of main.py unchanged (static file serving, etc.).

- [ ] **Step 5: Run tests**

```bash
cd backend && python -m pytest tests/test_task_api.py -v
```

Expected: 5 passed

- [ ] **Step 6: Run all non-network tests**

```bash
cd backend && python -m pytest -v -m "not network"
```

Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add backend/api/tasks.py backend/api/__init__.py backend/main.py backend/tests/test_task_api.py
git commit -m "feat: add task CRUD API with start/stop/test and scheduler lifecycle"
```

---

### Task 5: Frontend Tasks.vue

**Files:**
- Modify: `frontend/src/api/client.js`
- Replace: `frontend/src/views/Tasks.vue`

- [ ] **Step 1: Add task methods to API client**

Add to `api` object in `frontend/src/api/client.js`:

```js
  async listTasks() {
    return request('/tasks')
  },

  async createTask(data) {
    return request('/tasks', { method: 'POST', body: JSON.stringify(data) })
  },

  async updateTask(id, data) {
    return request(`/tasks/${id}`, { method: 'PUT', body: JSON.stringify(data) })
  },

  async deleteTask(id) {
    return request(`/tasks/${id}`, { method: 'DELETE' })
  },

  async startTask(id) {
    return request(`/tasks/${id}/start`, { method: 'POST' })
  },

  async stopTask(id) {
    return request(`/tasks/${id}/stop`, { method: 'POST' })
  },

  async testTask(id) {
    return request(`/tasks/${id}/test`, { method: 'POST' })
  },

  async getScreeners() {
    return request('/tasks/screeners')
  },
```

- [ ] **Step 2: Replace `frontend/src/views/Tasks.vue`**

Create a task management page with:
- Task list with status badges (running/stopped)
- Create task form with type selector and dynamic config fields
- Start/stop/test/delete actions per task
- Schedule description display

```vue
<template>
  <div>
    <div class="page-header">
      <h1>任务管理</h1>
      <p>创建和管理信号监控任务</p>
    </div>

    <button class="btn btn-primary" @click="showCreate = true" style="margin-bottom: 24px">
      创建任务
    </button>

    <!-- Create Form -->
    <div v-if="showCreate" class="card" style="margin-bottom: 24px">
      <h3 style="margin-bottom: 16px">创建任务</h3>
      <form @submit.prevent="handleCreate">
        <div class="form-row">
          <div class="form-group">
            <label>任务名称</label>
            <input v-model="form.name" placeholder="例如：BTC 信号监控" required />
          </div>
          <div class="form-group">
            <label>任务类型</label>
            <select v-model="form.type">
              <option value="watchlist_signal">关注列表信号监控</option>
              <option value="market_scan">全市场叠加扫描</option>
              <option value="anomaly_watch">异常行情监控</option>
              <option value="scheduled_shot">定时截图</option>
            </select>
          </div>
        </div>
        <div class="form-group">
          <label>调度周期</label>
          <select v-model="form.schedule">
            <option value="5m">每5分钟</option>
            <option value="15m">每15分钟</option>
            <option value="30m">每30分钟</option>
            <option value="1h">每小时</option>
            <option value="4h">每4小时</option>
            <option value="1d">每天</option>
            <option value="1w">每周</option>
          </select>
        </div>
        <div class="form-group">
          <label>推送通道</label>
          <select v-model="form.channel_id">
            <option :value="null">无</option>
            <option v-for="ch in channels" :key="ch.id" :value="ch.id">{{ ch.name }}</option>
          </select>
        </div>
        <div class="form-group">
          <label>动作</label>
          <div style="display: flex; gap: 16px">
            <label style="font-size: 14px; display: flex; align-items: center; gap: 6px; color: var(--text-primary)">
              <input type="checkbox" v-model="form.actions" value="text_summary" /> 文字推送
            </label>
            <label style="font-size: 14px; display: flex; align-items: center; gap: 6px; color: var(--text-primary)">
              <input type="checkbox" v-model="form.actions" value="chart_shot" /> 截图推送
            </label>
          </div>
        </div>
        <div class="form-actions">
          <button type="submit" class="btn btn-primary">创建</button>
          <button type="button" class="btn" @click="showCreate = false">取消</button>
        </div>
      </form>
    </div>

    <!-- Task List -->
    <div v-if="tasks.length === 0 && !showCreate" class="empty-state card">
      <svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <rect x="3" y="3" width="7" height="7" rx="1.5" />
        <rect x="14" y="3" width="7" height="7" rx="1.5" />
        <rect x="3" y="14" width="7" height="7" rx="1.5" />
        <rect x="14" y="14" width="7" height="7" rx="1.5" />
      </svg>
      <h3>暂无任务</h3>
      <p>创建你的第一个监控任务，开始追踪信号。</p>
    </div>

    <div v-for="t in tasks" :key="t.id" class="card task-card">
      <div class="task-header">
        <div class="task-info">
          <span class="task-name">{{ t.name }}</span>
          <span class="badge" :class="t.running ? 'badge-success' : 'badge-danger'">
            {{ t.running ? '运行中' : '已停止' }}
          </span>
          <span class="task-type">{{ typeLabel(t.type) }}</span>
          <span class="task-schedule">{{ t.schedule_desc }}</span>
        </div>
        <div class="task-actions">
          <button class="btn btn-sm" @click="testRun(t)" :disabled="t.testing">
            {{ t.testing ? '执行中...' : '测试' }}
          </button>
          <button v-if="!t.running" class="btn btn-sm" @click="startTask(t)">启动</button>
          <button v-else class="btn btn-sm" @click="stopTask(t)">停止</button>
          <button class="btn btn-sm" style="color: var(--danger)" @click="removeTask(t)">删除</button>
        </div>
      </div>
      <div v-if="t.testResult" class="test-result" :class="t.testResult.ok ? 'test-ok' : 'test-fail'">
        {{ t.testResult.ok ? '执行成功' : '执行失败: ' + (t.testResult.error || '') }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api/client.js'

const tasks = ref([])
const channels = ref([])
const showCreate = ref(false)
const form = ref({
  name: '', type: 'watchlist_signal', schedule: '1h',
  channel_id: null, actions: ['text_summary'],
  config: {},
})

const TYPE_LABELS = {
  watchlist_signal: '关注列表信号',
  market_scan: '全市场扫描',
  anomaly_watch: '异常行情',
  scheduled_shot: '定时截图',
}

function typeLabel(t) { return TYPE_LABELS[t] || t }

async function loadTasks() {
  tasks.value = (await api.listTasks()).map(t => ({ ...t, testing: false, testResult: null }))
}

async function loadChannels() {
  try { channels.value = await api.listChannels() } catch {}
}

async function handleCreate() {
  await api.createTask(form.value)
  showCreate.value = false
  form.value = { name: '', type: 'watchlist_signal', schedule: '1h', channel_id: null, actions: ['text_summary'], config: {} }
  await loadTasks()
}

async function startTask(t) {
  await api.startTask(t.id)
  await loadTasks()
}

async function stopTask(t) {
  await api.stopTask(t.id)
  await loadTasks()
}

async function testRun(t) {
  t.testing = true; t.testResult = null
  try { t.testResult = await api.testTask(t.id) }
  catch (e) { t.testResult = { ok: false, error: e.message } }
  finally { t.testing = false }
}

async function removeTask(t) {
  if (!confirm(`确认删除任务 "${t.name}"？`)) return
  await api.deleteTask(t.id)
  await loadTasks()
}

onMounted(() => { loadTasks(); loadChannels() })
</script>

<style scoped>
.form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.form-group { margin-bottom: 16px; }
.form-group label { display: block; margin-bottom: 6px; color: var(--text-secondary); font-size: 13px; font-weight: 600; }
.form-actions { display: flex; gap: 12px; margin-top: 8px; }
.task-card { margin-bottom: 12px; }
.task-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.task-info { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.task-name { font-weight: 600; font-size: 15px; }
.task-type { color: var(--text-tertiary); font-size: 13px; }
.task-schedule { color: var(--text-tertiary); font-size: 12px; }
.task-actions { display: flex; gap: 8px; flex-shrink: 0; }
.test-result { margin-top: 12px; padding: 8px 14px; border-radius: var(--radius-sm); font-size: 13px; }
.test-ok { background: var(--success-subtle); color: var(--success); }
.test-fail { background: var(--danger-subtle); color: var(--danger); }
</style>
```

- [ ] **Step 3: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/client.js frontend/src/views/Tasks.vue
git commit -m "feat: add task management UI with create, start/stop, and test"
```

---

### Task 6: Integration Verification

- [ ] **Step 1: Run all backend tests**

```bash
cd backend && source .venv/Scripts/activate && python -m pytest -v -m "not network"
```

Expected: all pass

- [ ] **Step 2: Build and deploy**

```bash
cd /c/Users/real/Desktop/WoHub && docker compose up -d --build
```

- [ ] **Step 3: Verify task API**

```bash
# Create a task
curl -s -X POST http://localhost:8080/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"name":"Test","type":"watchlist_signal","config":{},"actions":["text_summary"],"schedule":"1h"}' | python -m json.tool

# List tasks
curl -s http://localhost:8080/api/tasks | python -m json.tool

# Get screeners
curl -s http://localhost:8080/api/tasks/screeners | python -m json.tool
```

- [ ] **Step 4: Verify frontend**

Open http://localhost:8080, login, go to Tasks page. Verify:
- Create task form with type, schedule, channel selection
- Task list with running status
- Start/stop/test/delete buttons

- [ ] **Step 5: Stop containers**

```bash
docker compose down
```

---

## Phase 1e Deliverables

1. **Pine screener data source** with cross-analysis (screener overlap, resolution overlap, full overlap)
2. **6 screener JSON configs** migrated (divergence, overbought, oversold, volatility, shadows, trend_volume_spike)
3. **APScheduler integration** with cron triggers per resolution
4. **Task executor** handling 4 task types (watchlist_signal, market_scan, anomaly_watch, scheduled_shot)
5. **Task CRUD API** with start/stop/test endpoints
6. **Tasks.vue** management UI
7. **Signal recording** to signals table for data accumulation

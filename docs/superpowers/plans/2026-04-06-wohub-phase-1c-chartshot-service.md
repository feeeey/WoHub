# WoHub Phase 1c: ChartShot Screenshot Service — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate ChartShot's TradingView screenshot capability into `services/chartshot/` as an internal microservice with a clean REST API, accessible by the main WoHub backend.

**Architecture:** Flask app inside a Playwright Docker container. Single-threaded worker processes screenshot jobs sequentially (Playwright requires this). Cookies shared via Docker volume. Main WoHub backend calls the service via internal HTTP. Telegram integration is NOT included (Phase 1d).

**Tech Stack:** Python 3.13 / Flask / Playwright + Chromium / Docker (mcr.microsoft.com/playwright base image)

---

## Scope

**In scope (Phase 1c):**
- Core screenshot logic (open TradingView, wait for indicators, download image)
- Cookie management (load, save, test validity)
- Single-threaded worker queue
- REST API: screenshot, cookie management, health
- Playwright Dockerfile with Chromium
- WoHub backend client (`chart_shot_client.py`)

**Out of scope (later phases):**
- Telegram integration (Phase 1d)
- Preheat manager (optimization, add later if needed)
- Webhook parsing (WoHub task engine handles this)

---

## File Map

### ChartShot Service (`services/chartshot/`)

| File | Responsibility |
|------|---------------|
| `services/chartshot/requirements.txt` | Python dependencies |
| `services/chartshot/Dockerfile` | Playwright base image build |
| `services/chartshot/config.py` | Service configuration |
| `services/chartshot/cookies_manager.py` | TradingView cookie load/save/test |
| `services/chartshot/screenshot.py` | Core Playwright screenshot logic |
| `services/chartshot/worker.py` | Single-threaded job queue |
| `services/chartshot/main.py` | Flask app with REST API |

### WoHub Backend

| File | Responsibility |
|------|---------------|
| `backend/sources/chart_shot_client.py` | HTTP client to call ChartShot service |
| `backend/tests/test_chart_shot_client.py` | Client tests (mocked) |

### Root

| File | Change |
|------|--------|
| `docker-compose.yml` | Update chartshot service with Playwright image, shm_size, volumes |

---

### Task 1: ChartShot Project Setup

**Files:**
- Replace: `services/chartshot/requirements.txt`
- Replace: `services/chartshot/Dockerfile`
- Create: `services/chartshot/config.py`

- [ ] **Step 1: Replace `services/chartshot/requirements.txt`**

```
playwright>=1.50.0
flask>=3.0.0
httpx>=0.27.0
```

- [ ] **Step 2: Replace `services/chartshot/Dockerfile`**

```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.50.0-noble

ENV TZ=Asia/Shanghai
ENV DEBIAN_FRONTEND=noninteractive
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

COPY . ./
RUN mkdir -p output cookies

EXPOSE 5000
CMD ["python", "main.py"]
```

- [ ] **Step 3: Create `services/chartshot/config.py`**

```python
import os

COOKIE_DIR = os.path.join(os.path.dirname(__file__), "cookies")
COOKIE_FILE = "tradingview.py"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
CHART_LAYOUT_ID = os.environ.get("CHART_LAYOUT_ID", "ndpeiSwl")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "5000"))

TIMEFRAME_MAP = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "4h": "240", "8h": "480", "1d": "1D", "1w": "1W",
}

VALID_TIMEFRAMES = set(TIMEFRAME_MAP.keys())

SYMBOL_EXCHANGE_MAP = {
    "XAUUSD": "OANDA:XAUUSD",
    "XAGUSD": "OANDA:XAGUSD",
    "USDJPY": "FX:USDJPY",
}

MAX_RETRIES = 3
RETRY_BACKOFF = [3, 5, 8]
```

- [ ] **Step 4: Commit**

```bash
git add services/chartshot/requirements.txt services/chartshot/Dockerfile services/chartshot/config.py
git commit -m "feat(chartshot): set up project with Playwright Dockerfile and config"
```

---

### Task 2: Cookie Management

**Files:**
- Create: `services/chartshot/cookies_manager.py`

- [ ] **Step 1: Create `services/chartshot/cookies_manager.py`**

```python
import os
import sys
import importlib.util
import httpx
from config import COOKIE_DIR, COOKIE_FILE


def _load_module(filename):
    filepath = os.path.join(COOKIE_DIR, filename)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Cookie file not found: {filepath}")
    spec = importlib.util.spec_from_file_location("cookie_conf", filepath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_cookies(filename=None, domain=".tradingview.com"):
    mod = _load_module(filename or COOKIE_FILE)
    raw = getattr(mod, "cookies", None)
    if not raw or not isinstance(raw, dict):
        raise ValueError("No valid 'cookies' dict found in config")
    return [
        {"name": k, "value": v, "domain": domain, "path": "/"}
        for k, v in raw.items()
    ]


def load_headers(filename=None):
    mod = _load_module(filename or COOKIE_FILE)
    return getattr(mod, "headers", {})


def get_raw_cookie_string(filename=None):
    mod = _load_module(filename or COOKIE_FILE)
    raw = getattr(mod, "cookies", {})
    return "; ".join(f"{k}={v}" for k, v in raw.items())


def save_cookies_from_string(raw_cookie, filename=None):
    filename = filename or COOKIE_FILE
    filepath = os.path.join(COOKIE_DIR, filename)
    os.makedirs(COOKIE_DIR, exist_ok=True)

    # Parse cookie string
    pairs = {}
    for part in raw_cookie.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            pairs[k.strip()] = v.strip()

    # Preserve existing headers
    headers = {}
    try:
        mod = _load_module(filename)
        headers = getattr(mod, "headers", {})
    except Exception:
        pass

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"cookies = {repr(pairs)}\n\n")
        f.write(f"headers = {repr(headers)}\n")


def test_validity(filename=None):
    try:
        cookie_str = get_raw_cookie_string(filename)
        resp = httpx.get(
            "https://cn.tradingview.com/",
            headers={"Cookie": cookie_str, "User-Agent": "Mozilla/5.0"},
            follow_redirects=True,
            timeout=10,
        )
        text = resp.text
        if '"username":"' in text:
            start = text.index('"username":"') + len('"username":"')
            end = text.index('"', start)
            return {"valid": True, "username": text[start:end]}
        if '"isLoggedIn":true' in text:
            return {"valid": True, "username": "unknown"}
        return {"valid": False, "error": "Not logged in"}
    except Exception as e:
        return {"valid": False, "error": str(e)}
```

- [ ] **Step 2: Commit**

```bash
git add services/chartshot/cookies_manager.py
git commit -m "feat(chartshot): add cookie management module"
```

---

### Task 3: Core Screenshot Logic

**Files:**
- Create: `services/chartshot/screenshot.py`

This is the most complex file — migrated from the existing ChartShot `src/chartshot.py` with Telegram integration removed.

- [ ] **Step 1: Create `services/chartshot/screenshot.py`**

```python
import os
import time
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright, Page
from config import (
    OUTPUT_DIR, CHART_LAYOUT_ID, TIMEFRAME_MAP,
    SYMBOL_EXCHANGE_MAP, MAX_RETRIES, RETRY_BACKOFF,
)
from cookies_manager import load_cookies, load_headers

os.makedirs(OUTPUT_DIR, exist_ok=True)


def build_chart_url(symbol, timeframe=None):
    if ":" not in symbol:
        mapped = SYMBOL_EXCHANGE_MAP.get(symbol.upper())
        if mapped:
            symbol = mapped
        else:
            symbol = f"BINANCE:{symbol}.P"

    url = f"https://cn.tradingview.com/chart/{CHART_LAYOUT_ID}/?symbol={symbol}"
    if timeframe and timeframe in TIMEFRAME_MAP:
        url += f"&interval={TIMEFRAME_MAP[timeframe]}"
    return url


def _count_visible_spinners(page):
    return page.evaluate("""() => {
        const spinners = document.querySelectorAll(
            '.loader-spinner, .tv-spinner, [class*="spinner"], [class*="loading"]'
        );
        let count = 0;
        spinners.forEach(s => {
            const rect = s.getBoundingClientRect();
            const style = window.getComputedStyle(s);
            if (rect.width > 0 && rect.height > 0 &&
                style.display !== 'none' && style.visibility !== 'hidden') {
                count++;
            }
        });
        return count;
    }""")


def _has_calculation_timeout(page):
    return page.evaluate("""() => {
        const text = document.body.innerText || '';
        return text.includes('Calculation timed out') || text.includes('计算超时');
    }""")


def wait_for_indicators_ready(page, timeout=60):
    time.sleep(2)
    start = time.time()
    stable_count = 0
    spinners_ever_seen = False
    required_stable = 6

    while time.time() - start < timeout:
        spinners = _count_visible_spinners(page)
        if spinners > 0:
            spinners_ever_seen = True
            stable_count = 0
        else:
            stable_count += 1

        threshold = required_stable if spinners_ever_seen else 12
        if stable_count >= threshold:
            return not _has_calculation_timeout(page)

        if _has_calculation_timeout(page):
            return False

        time.sleep(0.5)

    return not _has_calculation_timeout(page)


def _click_screenshot_and_download(page, output_path):
    btn = page.locator("button:has(#header-toolbar-screenshot)")
    btn.click()
    time.sleep(1)

    download_btn = None
    for selector in [
        'div[data-name="save-chart-image"]',
        ':text("下载图片")',
        ':text("Download image")',
    ]:
        loc = page.locator(selector)
        if loc.count() > 0:
            download_btn = loc.first
            break

    if not download_btn:
        raise RuntimeError("Download button not found")

    with page.expect_download(timeout=15000) as dl_info:
        download_btn.click()

    download = dl_info.value
    download.save_as(str(output_path))
    return output_path


def capture_chart(symbol, timeframes, headless=True):
    """Capture TradingView chart screenshots for given symbol and timeframes.

    Returns list of file paths to saved screenshots.
    """
    try:
        cookies = load_cookies()
    except Exception as e:
        raise RuntimeError(f"Failed to load cookies: {e}")

    try:
        headers = load_headers()
    except Exception:
        headers = {}

    valid_tfs = []
    for tf in timeframes:
        if tf in TIMEFRAME_MAP and tf not in valid_tfs:
            valid_tfs.append(tf)

    if not valid_tfs:
        raise ValueError(f"No valid timeframes in: {timeframes}")

    results = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=headers.get("user-agent", "Mozilla/5.0"),
            locale="zh-CN",
        )
        context.add_cookies(cookies)

        pages = {}
        for tf in valid_tfs:
            url = build_chart_url(symbol, tf)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            pages[tf] = page

        for tf, page in pages.items():
            for attempt in range(MAX_RETRIES):
                ready = wait_for_indicators_ready(page)
                if ready:
                    break
                if attempt < MAX_RETRIES - 1:
                    backoff = RETRY_BACKOFF[attempt]
                    print(f"[{symbol}|{tf}] Retry {attempt + 1}, waiting {backoff}s")
                    time.sleep(backoff)
                    page.reload(wait_until="domcontentloaded")

            try:
                ts = time.strftime("%Y%m%d_%H%M%S")
                tmp_name = f"_tmp_{os.getpid()}_{uuid.uuid4().hex[:8]}.png"
                tmp_path = Path(OUTPUT_DIR) / tmp_name
                _click_screenshot_and_download(page, tmp_path)

                final_name = f"{symbol}_{tf}_{ts}.png"
                final_path = Path(OUTPUT_DIR) / final_name
                tmp_path.rename(final_path)
                results.append(str(final_path))
            except Exception as e:
                print(f"[{symbol}|{tf}] Screenshot failed: {e}")

        context.close()
        browser.close()

    return results
```

- [ ] **Step 2: Commit**

```bash
git add services/chartshot/screenshot.py
git commit -m "feat(chartshot): add core Playwright screenshot logic"
```

---

### Task 4: Worker Queue

**Files:**
- Create: `services/chartshot/worker.py`

- [ ] **Step 1: Create `services/chartshot/worker.py`**

```python
import queue
import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CaptureJob:
    symbol: str
    timeframes: list
    job_id: str = ""
    result: Optional[list] = field(default=None, repr=False)
    error: Optional[str] = field(default=None, repr=False)
    done_event: threading.Event = field(default_factory=threading.Event, repr=False)


class CaptureWorker:
    def __init__(self):
        self._queue = queue.Queue()
        self._thread = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print("[worker] Started")

    def stop(self):
        self._running = False
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=30)

    def submit(self, job: CaptureJob):
        self._queue.put(job)
        print(f"[worker] Queued {job.symbol} {job.timeframes} (depth={self._queue.qsize()})")

    def _run(self):
        from screenshot import capture_chart

        while self._running:
            try:
                job = self._queue.get(timeout=1)
            except queue.Empty:
                continue

            if job is None:
                break

            try:
                paths = capture_chart(job.symbol, job.timeframes)
                job.result = paths
            except Exception as e:
                job.error = str(e)
                print(f"[worker] Error: {e}")
            finally:
                job.done_event.set()

        print("[worker] Stopped")
```

- [ ] **Step 2: Commit**

```bash
git add services/chartshot/worker.py
git commit -m "feat(chartshot): add single-threaded capture worker queue"
```

---

### Task 5: Flask API

**Files:**
- Replace: `services/chartshot/main.py`

- [ ] **Step 1: Replace `services/chartshot/main.py`**

```python
import os
import uuid
from flask import Flask, jsonify, request, send_file
from config import HOST, PORT, OUTPUT_DIR, VALID_TIMEFRAMES
from worker import CaptureWorker, CaptureJob
from cookies_manager import (
    get_raw_cookie_string,
    save_cookies_from_string,
    test_validity,
)

app = Flask(__name__)
worker = CaptureWorker()


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "chartshot"})


@app.route("/api/screenshot", methods=["POST"])
def screenshot():
    """
    Request body:
    {
        "symbol": "BTCUSDT",
        "timeframes": ["1h", "4h"]
    }

    Response (sync, waits for completion):
    {
        "ok": true,
        "files": ["BTCUSDT_1h_20260406_120000.png", ...],
        "paths": ["/app/output/BTCUSDT_1h_20260406_120000.png", ...]
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "JSON body required"}), 400

    symbol = data.get("symbol", "").strip().upper()
    if not symbol:
        return jsonify({"ok": False, "error": "symbol is required"}), 400

    timeframes = data.get("timeframes", [])
    if not timeframes:
        return jsonify({"ok": False, "error": "timeframes is required"}), 400

    invalid = [tf for tf in timeframes if tf not in VALID_TIMEFRAMES]
    if invalid:
        return jsonify({"ok": False, "error": f"Invalid timeframes: {invalid}"}), 400

    job = CaptureJob(
        symbol=symbol,
        timeframes=timeframes,
        job_id=uuid.uuid4().hex[:8],
    )
    worker.submit(job)

    # Wait for completion (sync API — simplifies client logic)
    done = job.done_event.wait(timeout=120)
    if not done:
        return jsonify({"ok": False, "error": "Timeout waiting for screenshot"}), 504

    if job.error:
        return jsonify({"ok": False, "error": job.error}), 500

    files = []
    paths = []
    for p in (job.result or []):
        paths.append(p)
        files.append(os.path.basename(p))

    return jsonify({"ok": True, "files": files, "paths": paths})


@app.route("/api/screenshot/file/<filename>")
def get_screenshot_file(filename):
    """Serve a screenshot file by name."""
    filepath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.isfile(filepath):
        return jsonify({"error": "File not found"}), 404
    return send_file(filepath, mimetype="image/png")


@app.route("/api/cookies", methods=["GET"])
def get_cookies():
    try:
        raw = get_raw_cookie_string()
        return jsonify({"ok": True, "cookies": raw})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/cookies", methods=["PUT"])
def update_cookies():
    data = request.get_json()
    raw = data.get("cookies", "") if data else ""
    if not raw:
        return jsonify({"ok": False, "error": "cookies string required"}), 400
    try:
        save_cookies_from_string(raw)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/cookies/test", methods=["POST"])
def test_cookies():
    result = test_validity()
    return jsonify(result)


if __name__ == "__main__":
    worker.start()
    try:
        app.run(host=HOST, port=PORT, debug=False)
    finally:
        worker.stop()
```

- [ ] **Step 2: Commit**

```bash
git add services/chartshot/main.py
git commit -m "feat(chartshot): add Flask API with screenshot, cookie, and health endpoints"
```

---

### Task 6: Docker Compose Update

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Update `docker-compose.yml`**

Replace the `chartshot` service block. The full file should be:

```yaml
services:
  wohub:
    build:
      context: .
      dockerfile: backend/Dockerfile
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data
    environment:
      - TZ=UTC
      - APP_PASSWORD=${APP_PASSWORD:-admin}
      - SECRET_KEY=${SECRET_KEY:-change-me-in-production}
      - CHARTSHOT_URL=http://chartshot:5000
    depends_on:
      chartshot:
        condition: service_started
    restart: unless-stopped

  chartshot:
    build:
      context: ./services/chartshot
    shm_size: 2gb
    volumes:
      - ./data/screenshots:/app/output
      - ./data/cookies:/app/cookies
    environment:
      - TZ=Asia/Shanghai
    restart: unless-stopped
```

- [ ] **Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: update docker-compose with Playwright-based ChartShot service"
```

---

### Task 7: WoHub Backend Client

**Files:**
- Create: `backend/sources/chart_shot_client.py`
- Create: `backend/tests/test_chart_shot_client.py`

- [ ] **Step 1: Write tests**

Create `backend/tests/test_chart_shot_client.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from sources.chart_shot_client import ChartShotClient


@pytest.fixture
def client():
    return ChartShotClient("http://fake-chartshot:5000")


def test_health_check(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "ok", "service": "chartshot"}
    mock_resp.raise_for_status = MagicMock()

    with patch("sources.chart_shot_client.requests.get", return_value=mock_resp):
        result = client.health()
        assert result["status"] == "ok"


def test_screenshot_success(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "ok": True,
        "files": ["BTC_1h_20260406.png"],
        "paths": ["/app/output/BTC_1h_20260406.png"],
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("sources.chart_shot_client.requests.post", return_value=mock_resp):
        result = client.screenshot("BTCUSDT", ["1h"])
        assert result["ok"] is True
        assert len(result["files"]) == 1


def test_screenshot_error(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"ok": False, "error": "Timeout"}
    mock_resp.status_code = 504
    mock_resp.raise_for_status.side_effect = Exception("504")

    with patch("sources.chart_shot_client.requests.post", return_value=mock_resp):
        result = client.screenshot("BTCUSDT", ["1h"])
        assert result["ok"] is False


def test_get_screenshot_url(client):
    url = client.screenshot_url("BTC_1h_20260406.png")
    assert url == "http://fake-chartshot:5000/api/screenshot/file/BTC_1h_20260406.png"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && source .venv/Scripts/activate && python -m pytest tests/test_chart_shot_client.py -v
```

Expected: FAIL — module not found

- [ ] **Step 3: Implement the client**

Create `backend/sources/chart_shot_client.py`:

```python
import requests
from config import settings


class ChartShotClient:
    def __init__(self, base_url=None):
        self.base_url = (base_url or settings.chartshot_url).rstrip("/")

    def health(self):
        resp = requests.get(f"{self.base_url}/health", timeout=5)
        resp.raise_for_status()
        return resp.json()

    def screenshot(self, symbol, timeframes, timeout=120):
        try:
            resp = requests.post(
                f"{self.base_url}/api/screenshot",
                json={"symbol": symbol, "timeframes": timeframes},
                timeout=timeout,
            )
            return resp.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def screenshot_url(self, filename):
        return f"{self.base_url}/api/screenshot/file/{filename}"

    def get_cookies(self):
        resp = requests.get(f"{self.base_url}/api/cookies", timeout=5)
        return resp.json()

    def update_cookies(self, raw_cookie_string):
        resp = requests.put(
            f"{self.base_url}/api/cookies",
            json={"cookies": raw_cookie_string},
            timeout=5,
        )
        return resp.json()

    def test_cookies(self):
        resp = requests.post(f"{self.base_url}/api/cookies/test", timeout=15)
        return resp.json()


# Singleton
chartshot_client = ChartShotClient()
```

- [ ] **Step 4: Run tests**

```bash
cd backend && python -m pytest tests/test_chart_shot_client.py -v
```

Expected: 4 passed

- [ ] **Step 5: Run all non-network tests**

```bash
cd backend && python -m pytest -v -m "not network"
```

Expected: all pass (25 previous + 4 new = 29)

- [ ] **Step 6: Commit**

```bash
git add backend/sources/chart_shot_client.py backend/tests/test_chart_shot_client.py
git commit -m "feat: add ChartShot HTTP client for WoHub backend"
```

---

### Task 8: Integration Verification

- [ ] **Step 1: Run all backend tests**

```bash
cd backend && source .venv/Scripts/activate && python -m pytest -v -m "not network"
```

Expected: 29 passed

- [ ] **Step 2: Build frontend**

```bash
cd frontend && npm run build
```

Expected: no errors

- [ ] **Step 3: Build Docker images**

```bash
cd /c/Users/real/Desktop/WoHub && docker compose build
```

Expected: both images build. ChartShot image will be larger (~1.5GB due to Playwright/Chromium).

- [ ] **Step 4: Start containers**

```bash
docker compose up -d
```

- [ ] **Step 5: Verify ChartShot health**

```bash
docker compose exec chartshot python -c "import urllib.request; r = urllib.request.urlopen('http://localhost:5000/health'); print(r.read().decode())"
```

Expected: `{"service":"chartshot","status":"ok"}`

- [ ] **Step 6: Verify main service can reach ChartShot**

```bash
docker compose exec wohub python -c "
import requests
r = requests.get('http://chartshot:5000/health', timeout=5)
print(r.json())
"
```

Expected: `{'status': 'ok', 'service': 'chartshot'}`

- [ ] **Step 7: Stop containers**

```bash
docker compose down
```

- [ ] **Step 8: Commit if any changes needed**

```bash
git status
```

If clean, Phase 1c is complete.

---

## Phase 1c Deliverables

1. **ChartShot microservice** with Playwright screenshot capability
2. **REST API**: POST /api/screenshot, GET/PUT /api/cookies, POST /api/cookies/test
3. **Single-threaded worker** for sequential Playwright operations
4. **Docker image** based on Playwright with Chromium
5. **WoHub backend client** (`chart_shot_client.py`) with 4 tests
6. **Cookie management** with load/save/test functionality
7. **Total backend tests**: 29 (non-network)

## Notes

- Screenshot requests are **synchronous** (wait up to 120s). This is simple and correct for the task engine use case — a task triggers a screenshot and waits for the result.
- Preheat optimization is intentionally deferred. If performance becomes an issue, it can be added later without changing the API.
- TradingView cookies must be manually configured via the cookie API or by mounting a cookie file to `data/cookies/tradingview.py`.

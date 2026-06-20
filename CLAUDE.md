# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WoHub is a cryptocurrency trading signal aggregation platform. It runs TradingView Pine Screeners against watchlists, detects signals across multiple timeframes, and pushes notifications to Telegram/Discord. It also includes a Binance USDT-M futures trading terminal (structure-based position planning + bracket orders with stop-loss recovery).

## Commands

### Development

```bash
# Backend (from backend/)
pip install -e .              # Install dependencies
python main.py                # Run on :8080

# Frontend (from frontend/)
npm install
npm run dev                   # Vite dev server on :5173, proxies /api to :8080
npm run build                 # Build to dist/

# Docker (recommended)
docker-compose up             # App on :7756, ChartShot on :5000
```

### Testing

```bash
cd backend
pytest                        # All tests
pytest -m "not network"       # Skip live API tests
```

## Architecture

**Backend:** FastAPI (Python 3.11+) with SQLite (WAL mode) and APScheduler for background jobs.
**Frontend:** Vue 3 SPA with Vue Router, built by Vite. Production build is served as static files by the backend.
**Services:** ChartShot (Flask + Playwright for TradingView screenshots), deployed alongside via docker-compose.

### Key directories

- `backend/api/` — FastAPI routers: health, market, channels, tasks, settings, scanner, screenshots, klines, trading
- `backend/sources/` — Data fetching: `pine_screener.py` (TradingView), individual exchange clients (Binance, OKX, Bybit, Bitget), `chart_shot_client.py`
- `backend/tasks/` — `scheduler.py` (APScheduler cron/interval jobs), `executor.py` (task execution pipeline), `tracker.py` (signal outcome tracking at 1h/4h/24h)
- `backend/channels/` — Notification dispatch: `telegram.py`, `discord.py`, `sender.py`
- `backend/trading/` — Binance USDT-M client, credential encryption, order service (bracket + SL recovery), position planning
- `backend/klines/` — Candlestick fetch, pattern detection, classification, market structure (pivots, ATR)
- `backend/screeners/` — JSON configs for Pine screener filters (oscillator/, trend/)
- `frontend/src/views/` — Vue pages: Tasks, Scanner, Market, Trade, Channels, Settings, Login
- `services/chartshot/` — Standalone screenshot microservice

### Task execution flow

1. Scheduler triggers a task (cron or interval)
2. `executor.py` calls `pine_screener.run_screener()` for each screener x timeframe combo (rate-limited: 1 req/2 sec)
3. Cross-analysis: detects multi-screener overlap and multi-timeframe confluence
4. Sends results via configured channel (Telegram/Discord)
5. Optionally captures ChartShot screenshots
6. Persists signals, snapshots, push logs to SQLite

### Database

Single SQLite file at `data/wohub.db`. Schema defined in `backend/database.py` (SCHEMA constant). Key tables: channels, tasks, signals, snapshots, outcomes, push_logs, screenshots, trading_credentials, trading_orders.

### Auth

Cookie-based sessions using `itsdangerous.URLSafeTimedSerializer`. Single shared password set via `APP_PASSWORD` env var.

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `APP_PASSWORD` | `admin` | Login password |
| `SECRET_KEY` | `change-me-in-production` | Session signing key |
| `DB_PATH` | `data/wohub.db` | SQLite database path |
| `CHARTSHOT_URL` | `http://chartshot:5000` | Screenshot service URL |
| `DEBUG` | `false` | Enable debug mode |
| `PROXY_ENABLED` | `false` | Proxy for TradingView API |
| `PROXY_HOST` | `host.docker.internal` | Proxy host |
| `PROXY_PORT` | `24000` | Proxy port |
| `CACHE_TTL` | `15` | Market data cache TTL (seconds) |
| `MIN_VOLUME_24H` | `100000` | Minimum 24h volume filter |

## Conventions

- Backend uses Pydantic models for request validation in API routes
- Pine screener rate limiting enforced with thread locks (2-second intervals)
- Retry logic: 3 retries with exponential backoff [3s, 5s, 8s] for TradingView calls
- System logs use an in-memory ring buffer (200 entries max) via `app_logger.py`
- Frontend dark/light theme toggle persisted to localStorage

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
- `backend/tasks/` — `scheduler.py` (APScheduler cron/interval jobs), `executor.py` (task execution pipeline), `tracker.py` + `outcome_poller.py` (persistent signal outcome tracking at 1h/4h/24h, restart-safe)
- `backend/agent/` — chat agent: `chat/` (store/events/runtime/worker/vision/semantics/prompts), `tools.py` (read-only, throttled), `decider.py` (RuleDecider — task-pipeline threshold logic), `config.py` (llm_channels 渠道 CRUD + 双槽位渠道解析 + Fernet key), `llm.py`, `validator.py` (stub)
- `backend/channels/` — Notification dispatch: `telegram.py`, `discord.py`, `sender.py`
- `backend/trading/` — Binance USDT-M client, credential encryption, order service (bracket + SL recovery), position planning
- `backend/klines/` — Candlestick fetch, pattern detection, classification, market structure (pivots, ATR)
- `backend/screeners/` — JSON configs for Pine screener filters (oscillator/, trend/)
- `frontend/src/views/` — Vue pages: Tasks, Scanner, Market, Trade, Chat (agent), Channels, Settings, Login
- `services/chartshot/` — Standalone screenshot microservice

### Task execution flow

1. Scheduler triggers a task (cron or interval)
2. `executor.py` calls `pine_screener.run_screener()` for each screener x timeframe combo (rate-limited: 1 req/2 sec)
3. `RuleDecider.decide()` (the decision seam in `backend/agent/decider.py`) applies overlap/confluence thresholds
4. Sends results via configured channel (Telegram/Discord)
5. Optionally captures ChartShot screenshots
6. Persists signals, snapshots (+ due `outcome_checks`), push logs to SQLite
7. Signals are available to the chat agent's tools (screener scan, signal history) on demand — the task pipeline itself never queues or invokes the agent

### Chat agent

Conversational agent at `/agent` (Manus-style): multi-session chat persisted to
SQLite, background worker drains `chat_turns`, events append to `chat_events`,
SSE stream (`GET /api/chat/sessions/{id}/stream?after=N`) is a resumable
observation window. Tools are read-only (screener scan with progress events,
klines/indicators/structure, market snapshot/overview, signal history,
ChartShot capture + vision relay, position-plan preview, account overview) and
throttled; per-turn `max_tool_calls` + `deep_dive_limit` budgets. Screener
semantics profiles live in `screener_semantics` (Settings-editable, injected
into the system prompt). Vision uses a separate `vision_model` slot (same
provider/key). Red lines: `backend/agent/` never imports order-placing
functions; execution always goes through the human-confirmed Trade page
(`/trade?symbol=…&direction=…` prefill). Design docs:
`docs/superpowers/specs/2026-07-04-chat-agent-design.md`.

### Database

Single SQLite file at `data/wohub.db`. Schema defined in `backend/database.py` (SCHEMA constant, append-only — editing existing CREATE TABLE bodies is a silent no-op). Key tables: channels, tasks, signals, snapshots, outcomes, outcome_checks, push_logs, screenshots, trading_credentials, trading_orders, agent_config, agent_runs (dormant — retained to avoid migration risk, no code writes), agent_decisions (dormant), chat_sessions, chat_messages, chat_turns, chat_events, screener_semantics, llm_channels.

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
| `CHAT_UPLOADS_DIR` | `data/chat_uploads` | Chat 图片上传目录 |

## Conventions

- Backend uses Pydantic models for request validation in API routes
- Pine screener rate limiting enforced with thread locks (2-second intervals)
- Retry logic: 3 retries with exponential backoff [3s, 5s, 8s] for TradingView calls
- System logs use an in-memory ring buffer (200 entries max) via `app_logger.py`
- Frontend dark/light theme toggle persisted to localStorage

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
- `backend/agent/` — LLM decision layer: `decider.py` (Decider seam, RuleDecider baseline), `agent_decider.py` (PydanticAI tool loop), `tools.py` (read-only tools with throttle/budget), `worker.py` (queue-draining daemon thread), `queue.py`, `config.py` (Fernet-encrypted LLM key), `validator.py` (StrategyValidator stub)
- `backend/channels/` — Notification dispatch: `telegram.py`, `discord.py`, `sender.py`
- `backend/trading/` — Binance USDT-M client, credential encryption, order service (bracket + SL recovery), position planning
- `backend/klines/` — Candlestick fetch, pattern detection, classification, market structure (pivots, ATR)
- `backend/screeners/` — JSON configs for Pine screener filters (oscillator/, trend/)
- `frontend/src/views/` — Vue pages: Tasks, Scanner, Market, Trade, Agent (decision review), Channels, Settings, Login
- `services/chartshot/` — Standalone screenshot microservice

### Task execution flow

1. Scheduler triggers a task (cron or interval)
2. `executor.py` calls `pine_screener.run_screener()` for each screener x timeframe combo (rate-limited: 1 req/2 sec)
3. `RuleDecider.decide()` (the decision seam in `backend/agent/decider.py`) applies overlap/confluence thresholds; baseline decisions are persisted to `agent_runs`/`agent_decisions` on every run
4. Sends results via configured channel (Telegram/Discord)
5. Optionally captures ChartShot screenshots
6. Persists signals, snapshots (+ due `outcome_checks`), push logs to SQLite
7. If the task's actions include `agent_decide` and the agent is enabled, the batch context is queued to `agent_runs`; a background worker thread runs the PydanticAI decider (read-only tools, per-signal verdicts with direction/confidence/reasons) — never inline in the scheduler thread, never placing orders

### Agent decision layer

LLM verdicts are research-only: the agent proposes `direction/confidence/reasons` per signal; execution always goes through the human-confirmed Trade page pipeline. Config (provider openai/anthropic, encrypted API key) lives in the `agent_config` table via Settings. Review UI at `/agent` shows runs, verdicts, tool traces, 1h/4h/24h outcomes (direction-aware), human ratings, and agent-vs-rule-baseline stats (`GET /api/agent/stats`). Red lines: `backend/agent/` must never import order-placing functions; agent tools are read-only and throttled. Design docs: `docs/superpowers/specs/2026-07-02-agent-decision-layer-design.md`.

### Database

Single SQLite file at `data/wohub.db`. Schema defined in `backend/database.py` (SCHEMA constant, append-only — editing existing CREATE TABLE bodies is a silent no-op). Key tables: channels, tasks, signals, snapshots, outcomes, outcome_checks, push_logs, screenshots, trading_credentials, trading_orders, agent_config, agent_runs, agent_decisions.

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

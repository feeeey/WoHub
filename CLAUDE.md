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
- `backend/sources/` — Data fetching: `pine_screener.py` (TradingView), individual exchange clients (Binance, OKX, Bybit, Bitget)
- `backend/screenshots/` — 截图模块：`service.py`（唯一截图入口 `capture()` + 记录查询/删除）、`dispatch.py`（多渠道推送 + push_logs）、`client.py`（ChartShot HTTP 客户端）、`pipeline.py`（任务流水线兼容封装）
- `backend/tasks/` — `scheduler.py` (APScheduler cron/interval jobs), `executor.py` (task execution pipeline), `tracker.py` + `outcome_poller.py` (persistent signal outcome tracking at 1h/4h/24h, restart-safe)
- `backend/agent/` — chat agent: `chat/` (store/events/runtime/worker/vision/semantics/prompts), `tools.py` (read-only, throttled), `decider.py` (RuleDecider — task-pipeline threshold logic), `config.py` (llm_channels 渠道 CRUD + 双槽位渠道解析 + Fernet key), `llm.py`, `outcome_stats.py` (信号后验统计聚合，闭环数据侧), `validator.py` (StrategyValidator 接口 + OutcomeValidator 实现)
- `backend/evals/` — agent 行为评测（区别于 tests/ 的管道测试）：三层评分（L1 工具选择 / L2 轨迹效率 / L3 答案质量规则），金标用例 `golden/*.json`，`python -m evals` 离线按 prompt_version×model 分桶打分存量轨迹，`--live` 用 fixtures 固定工具数据金标实跑，`extract` 从真实轨迹提取用例骨架
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
5. Optionally captures ChartShot screenshots (`chart_shot` action，不再要求配了推送渠道——没渠道时只截图存档)
6. Persists signals, snapshots (+ due `outcome_checks`), push logs to SQLite
7. Signals are available to the chat agent's tools (screener scan, signal history) on demand — the task pipeline itself never queues or invokes the agent

### Screenshots

统一入口是 `screenshots.service.capture(symbol, timeframes, task_id=…)`：归一化标的
（`BINANCE:BTCUSDT.P` → `BTCUSDT`，非币安前缀原样保留）、校验周期、调 ChartShot、
落库、返回结构化 shots。executor、chat agent 的 `capture_chart`、REST 接口都走它，
所以任何来源的截图都能在列表里查到、重推、清理。

推送走 `screenshots.dispatch`：`push_shots()` 逐渠道隔离失败（一张图失败不影响同渠道
其余图，一个渠道失败不影响其他渠道），每渠道汇总写一条 push_logs；`capture_and_push()`
是截图+推送的组合入口。Telegram / Discord 的差异在 `channels/` 层已抹平，webhook 类型
因为没有图片上传语义会被跳过。

REST：`POST /api/screenshots/capture`（同步阻塞，ChartShot 串行渲染可能耗时 1–2 分钟）、
`GET /api/screenshots`（列表，支持 symbol/task_id/timeframe 筛选）、
`GET /api/screenshots/file/{filename}`（文件名白名单 + 路径逃逸二次校验）、
`POST /api/screenshots/{id}/push`（重推）、`DELETE /api/screenshots/{id}`（删行+删文件）。
手动截图入口在 Settings 页的 ChartShot 卡片。

注意 `screenshots.task_id` 有外键约束：悬空 id 会让落库失败（图已拍出来但检索不到），
所以 API 层先校验任务存在。ChartShot 侧的 DOM 选择器和 `CHART_LAYOUT_ID` 绑定
TradingView 页面结构，改版会静默失败（返回 ok 但少文件）——`capture()` 会把缺失的
周期记进 `errors`。

### Chat agent

Conversational agent at `/agent` (Manus-style): multi-session chat persisted to
SQLite, background worker drains `chat_turns`, events append to `chat_events`,
SSE stream (`GET /api/chat/sessions/{id}/stream?after=N`) is a resumable
observation window. Tools are read-only (screener scan with progress events,
klines/indicators/structure, market snapshot/overview, signal history,
ChartShot capture + vision relay, position-plan preview, account overview) and
throttled; per-turn `max_tool_calls` + `deep_dive_limit` budgets. Screener
semantics profiles live in `screener_semantics` (Settings-editable, injected
into the system prompt). Vision uses a separate `vision_model` slot on its
own channel (`vision_channel_id`, NULL = follow main channel). Red lines:
`backend/agent/` never imports order-placing
functions; execution always goes through the human-confirmed Trade page
(`/trade?symbol=…&direction=…` prefill). Design docs:
`docs/superpowers/specs/2026-07-04-chat-agent-design.md`.

**Outcome 闭环**：`agent/outcome_stats.py` 把 signals×outcomes 按筛选器
label 聚合成后验统计（方向盲原始收益，10 分钟 TTL 缓存），两处消费：
system prompt 的语义档案每条附带 `↳ 近90天后验（n=…）` 行（样本 <5 显式标
「样本不足」而不给数字）；chat 工具 `get_screener_stats` 回答"哪个筛选器
最近靠谱"。`OutcomeValidator`（validator.py）用同一数据验证语义档案的 bias
声明：连续折分段一致性 + 精确二项检验 + Bonferroni 校正，样本不足显式
not_validated——pass 含义是「方向声明与后验分布显著一致」，不是可盈利。

**行为评测**：改 prompt/换模型前后跑 `python -m evals`（离线，存量轨迹按
prompt_version×model 分桶）与 `python -m evals --live`（金标实跑，工具数据
用 evals/fixtures.py 固定，产生真实 LLM 费用）。金标用例在 `evals/golden/`，
工具名用 trace 内部名（`_tool()` 的 name 参数，非注册函数名）。评分权重与
规则见 `evals/scoring.py`；system prompt 里新增的行为承诺应同步加进 L3 规则。
Settings 页有评测卡片（历史落 `eval_runs` 表，带前后对比）；金标实跑在
**子进程**里执行（`evals/service.py`）——fixtures 对 agent.tools 的 patch 是
进程级的，进程内跑会让并发的 chat 轮次拿到假行情。

### Database

Single SQLite file at `data/wohub.db`. Schema defined in `backend/database.py` (SCHEMA constant, append-only — editing existing CREATE TABLE bodies is a silent no-op). Key tables: channels, tasks, signals, snapshots, outcomes, outcome_checks, push_logs, screenshots (task_id 由 `_migrate()` 补加——只按 signal_id 级联删除会漏掉匹配不到信号的行), trading_credentials, trading_orders, agent_config, agent_runs (dormant — retained to avoid migration risk, no code writes), agent_decisions (dormant), chat_sessions, chat_messages, chat_turns, chat_events, screener_semantics, llm_channels, eval_runs (评测历史).

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

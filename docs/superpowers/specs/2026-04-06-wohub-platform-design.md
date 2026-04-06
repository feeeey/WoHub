# WoHub 平台设计文档

> 将 CryptoFuturesHub、pine-screener、ChartShot 三个独立工具重新设计为统一的加密货币技术分析信号监控与推送管理平台。

## 设计原则

- **纯技术分析**：数据源仅包含价格、成交量、技术指标。不接入新闻、情绪等消息面数据。
- **任务驱动**：平台的核心管理单元是"任务"，所有监控、触发、推送围绕任务组织。
- **数据闭环**：每个信号不仅记录触发，还追踪后续价格变化，为未来量化提供基础。

---

## 一、核心概念模型

```
[检测层]                    [分析层]           [推送层]

 关注列表信号监控 ──┐                      ┌→ 文字汇总
 全市场指标扫描  ──┤──→ 条件判断 ──→ AI分析 ──┤→ 截图推送  ──→ Telegram/Discord
 涨跌幅/费率异常 ──┘   (叠加/共振/         └→ AI解读报告
                       罕见度判定)
```

四个核心概念：

| 概念 | 说明 |
|------|------|
| **监控任务（Monitor）** | 定义"看什么" — 数据源 + 标的范围 + 扫描周期 |
| **触发规则（Trigger）** | 定义"什么算异常" — 阈值、叠加条件、罕见度 |
| **动作（Action）** | 定义"怎么推" — 文字汇总 / 截图 / AI分析，可组合 |
| **通道（Channel）** | 定义"推给谁" — Telegram群、Discord频道、Webhook |

---

## 二、系统架构

```
┌─────────────────────────────────────────────────┐
│                   Vue 3 前端                      │
│  ┌──────────┬──────────┬──────────┬───────────┐  │
│  │ 任务管理  │ 市场看板  │ 推送通道  │ 系统配置   │  │
│  └──────────┴──────────┴──────────┴───────────┘  │
└──────────────────┬──────────────────────────────┘
                   │ REST API
┌──────────────────▼──────────────────────────────┐
│              FastAPI 主服务                        │
│                                                   │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────┐ │
│  │  任务调度器   │  │  数据源适配器  │  │ 推送管理  │ │
│  │ (APScheduler)│  │ ·交易所行情   │  │ ·Telegram│ │
│  │              │  │ ·Pine筛选    │  │ ·Discord │ │
│  │              │  │ ·资金费率    │  │ ·Webhook │ │
│  └──────┬───────┘  └──────┬──────┘  └────┬─────┘ │
│         │                 │              │        │
│  ┌──────▼─────────────────▼──────────────▼─────┐ │
│  │            触发引擎 (条件判断)                  │ │
│  └─────────────────────┬───────────────────────┘ │
│                        │ (Phase 2)                │
│  ┌─────────────────────▼───────────────────────┐ │
│  │            AI 分析层 (LLM)                     │ │
│  └─────────────────────────────────────────────┘ │
└──────────────────┬──────────────────────────────┘
                   │ 内部 API
┌──────────────────▼──────────────────────────────┐
│         ChartShot 截图服务 (独立容器)               │
│         Playwright + Chromium                     │
└─────────────────────────────────────────────────┘
```

关键技术选型：

| 组件 | 选型 | 理由 |
|------|------|------|
| 后端框架 | FastAPI | 异步支持好，pine-screener 已验证 |
| 前端框架 | Vue 3 + Vite | 轻量、单文件组件、适合多模块管理平台 |
| 数据库 | SQLite（Phase 1）| 零运维，查询能力满足需求，避免后期JSON迁移 |
| 任务调度 | APScheduler | pine-screener 已验证可行 |
| 截图服务 | Playwright + Chromium | ChartShot 现有方案，独立容器部署 |
| 部署 | Docker Compose | 双容器：主服务 + ChartShot |

---

## 三、任务类型

### 3.1 关注列表信号监控 (`watchlist_signal`)

对 TradingView 关注列表，监控指定指标是否触发。

| 配置项 | 说明 | 示例 |
|--------|------|------|
| 关注列表 | TradingView watchlist | "我的自选" |
| 指标 | 一个或多个 Pine screener | divergence, oversold |
| 时间周期 | 一个或多个 | 1h, 4h |
| 扫描时机 | K线收盘前定时 | 收盘前2分钟 |
| 动作 | 触发时执行什么 | 文字推送 + 截图推送 |

### 3.2 全市场叠加扫描 (`market_scan`)

全交易所合约，发现罕见多指标叠加信号。

| 配置项 | 说明 | 示例 |
|--------|------|------|
| 交易所 | 扫描范围 | Binance全合约 |
| 指标组合 | 多个 Pine screener | divergence + oversold + volume_spike |
| 时间周期 | 一个或多个 | 1h, 4h |
| 叠加阈值 | ≥N 个指标同时命中 | ≥2 |
| 扫描周期 | 定时间隔 | 每4h |
| 动作 | 整体结果→文字汇总；罕见标的→截图 | 叠加≥3时截图 |

### 3.3 异常行情监控 (`anomaly_watch`)

涨跌幅或资金费率异常时，检查是否有关键信号配合。

| 配置项 | 说明 | 示例 |
|--------|------|------|
| 监控指标 | 涨跌幅 / 资金费率 | 24h涨幅 |
| 异常阈值 | 触发条件 | 涨幅 > 10% |
| 联动检查 | 异常出现后自动跑哪些 Pine 指标 | divergence, overbought |
| 扫描周期 | | 每15分钟 |
| 动作 | 有信号配合时推送 | 文字 + 截图 |

### 3.4 定时截图 (`scheduled_shot`)

固定标的、固定时间，定期截图推送。

| 配置项 | 说明 | 示例 |
|--------|------|------|
| 标的列表 | 指定币种 | BTC, ETH, SOL |
| 时间周期 | 截图的K线周期 | 1h, 4h |
| 执行时间 | cron 表达式 | 每小时整点 |
| 动作 | | 截图推送 |

### 动作类型

动作可组合，按顺序执行：

| 动作 | 说明 |
|------|------|
| `text_summary` | 文字汇总（命中标的列表、指标、数值） |
| `chart_shot` | 调用 ChartShot 服务截图 |
| `ai_analysis` | AI 解读信号含义（Phase 2，预留接口） |

---

## 四、数据库设计

SQLite，所有时间存 UTC。

```sql
-- 推送通道配置
channels (
  id INTEGER PRIMARY KEY,
  type TEXT NOT NULL,              -- telegram / discord / webhook
  name TEXT NOT NULL,
  config_json TEXT NOT NULL,       -- {bot_token, chat_id, ...}
  enabled INTEGER DEFAULT 1,
  created_at TEXT, updated_at TEXT
)

-- 监控任务
tasks (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  type TEXT NOT NULL,              -- watchlist_signal / market_scan / anomaly_watch / scheduled_shot
  config_json TEXT NOT NULL,       -- 各类型差异化配置
  actions_json TEXT NOT NULL,      -- ["text_summary", "chart_shot"]
  channel_id INTEGER REFERENCES channels(id),
  schedule TEXT NOT NULL,          -- cron表达式或间隔秒数
  enabled INTEGER DEFAULT 0,
  created_at TEXT, updated_at TEXT
)

-- 信号记录（核心沉淀表）
signals (
  id INTEGER PRIMARY KEY,
  task_id INTEGER REFERENCES tasks(id),
  symbol TEXT NOT NULL,
  exchange TEXT NOT NULL,
  indicator TEXT NOT NULL,         -- divergence / oversold / ...
  timeframe TEXT NOT NULL,         -- 1h / 4h / ...
  signal_type TEXT,                -- bullish / bearish
  triggered_at TEXT NOT NULL
)

-- 触发时市场快照
snapshots (
  id INTEGER PRIMARY KEY,
  signal_id INTEGER REFERENCES signals(id),
  price REAL,
  volume_24h REAL,
  change_24h REAL,
  funding_rate REAL,
  captured_at TEXT NOT NULL
)

-- 信号后续追踪（数据闭环）
outcomes (
  id INTEGER PRIMARY KEY,
  signal_id INTEGER REFERENCES signals(id),
  price_1h REAL, price_4h REAL, price_24h REAL,
  change_1h REAL, change_4h REAL, change_24h REAL,
  tracked_at TEXT NOT NULL
)

-- 推送记录
push_logs (
  id INTEGER PRIMARY KEY,
  task_id INTEGER REFERENCES tasks(id),
  channel_id INTEGER REFERENCES channels(id),
  content_text TEXT,
  image_paths TEXT,                -- JSON array
  ai_analysis TEXT,
  status TEXT NOT NULL,            -- success / failed
  error_message TEXT,
  pushed_at TEXT NOT NULL
)

-- 截图文件记录
screenshots (
  id INTEGER PRIMARY KEY,
  signal_id INTEGER REFERENCES signals(id),
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  file_path TEXT NOT NULL,
  created_at TEXT NOT NULL
)
```

数据闭环流程：

```
信号触发 → 写入 signals + snapshots
        → 触发推送 → 写入 push_logs
        → 注册延迟任务：1h/4h/24h 后回查价格 → 写入 outcomes
```

---

## 五、前端模块

Vue 3 单页应用，侧边导航，四个主模块：

### 5.1 任务管理（核心页面）

- **任务列表**：卡片/表格展示，状态标签（运行中/已停止），快捷启停
- **创建任务**：选择类型后动态渲染配置表单
- **任务详情**：执行历史时间线、命中信号列表、推送记录、基础统计

### 5.2 市场看板

CryptoFuturesHub 功能平移：
- 资金费率排行
- 涨幅榜 / 跌幅榜
- 跨交易所对比

### 5.3 推送通道

- 通道列表（Telegram / Discord / Webhook）
- 添加/编辑/测试推送
- 推送历史日志

### 5.4 系统设置

- TradingView Cookie 管理（共用）
- ChartShot 服务状态
- 全局参数（默认扫描周期、最小成交量过滤等）
- 密码认证配置

---

## 六、项目结构

```
WoHub/
├── docker-compose.yml
│
├── backend/
│   ├── main.py                      # FastAPI 入口
│   ├── config.py                    # 全局配置
│   ├── database.py                  # SQLite 连接 + 表初始化
│   ├── sources/                     # 数据源适配器
│   │   ├── exchanges.py             # 交易所行情/费率
│   │   ├── pine_screener.py         # Pine 指标筛选
│   │   └── chart_shot_client.py     # ChartShot HTTP 客户端
│   ├── tasks/                       # 任务引擎
│   │   ├── scheduler.py             # APScheduler 调度
│   │   ├── executor.py              # 执行器
│   │   └── tracker.py               # 信号后续追踪
│   ├── triggers/                    # 触发规则
│   │   ├── threshold.py             # 阈值判断
│   │   └── overlap.py               # 叠加判断
│   ├── actions/                     # 动作执行器
│   │   ├── text_summary.py          # 文字汇总
│   │   ├── chart_shot.py            # 截图调用
│   │   └── ai_analysis.py           # AI 分析（Phase 2 预留）
│   ├── channels/                    # 推送通道
│   │   ├── telegram.py
│   │   ├── discord.py
│   │   └── webhook.py
│   ├── api/                         # API 路由
│   │   ├── tasks.py
│   │   ├── market.py
│   │   ├── channels.py
│   │   └── settings.py
│   └── data/                        # 运行时数据（Docker volume）
│       ├── wohub.db
│       └── cookies.json
│
├── chartshot/                       # 截图服务（独立容器）
│   ├── main.py
│   ├── src/
│   │   ├── chartshot.py             # Playwright 截图逻辑
│   │   ├── preheat.py               # 预热管理
│   │   └── worker.py                # 截图任务队列
│   ├── Dockerfile
│   └── cookies/
│
├── frontend/                        # Vue 3 前端
│   ├── index.html
│   ├── vite.config.js
│   ├── src/
│   │   ├── App.vue
│   │   ├── views/
│   │   │   ├── Tasks.vue
│   │   │   ├── TaskDetail.vue
│   │   │   ├── Market.vue
│   │   │   ├── Channels.vue
│   │   │   └── Settings.vue
│   │   ├── components/
│   │   └── api/
│   └── package.json
│
└── docs/
```

---

## 七、部署架构

Docker Compose 双容器：

```yaml
services:
  wohub:
    build: .
    ports: ["8080:8080"]
    volumes:
      - ./data:/app/data
    environment:
      - TZ=UTC

  chartshot:
    build: ./chartshot
    shm_size: 2gb
    volumes:
      - ./data/screenshots:/app/output
      - ./data/cookies.json:/app/cookies/cookies.json
    environment:
      - TZ=UTC
    # 仅内部网络通信，不暴露端口
```

- TradingView cookies 通过 volume 共享
- 截图文件存到共享 volume
- 用户只接触 8080 一个端口

---

## 八、分阶段交付

| Phase | 内容 | 依赖 |
|-------|------|------|
| **1a** | 项目脚手架 — FastAPI + Vue 3 + SQLite + Docker Compose 空壳 | 无 |
| **1b** | 市场看板 — CryptoFuturesHub 功能迁入 | 1a |
| **1c** | ChartShot 服务 — 截图能力迁入独立容器 | 1a |
| **1d** | 推送通道 — Telegram 推送 + 通道管理页面 | 1a |
| **1e** | 任务引擎核心 — 调度器 + 执行器 + Pine 筛选迁入 | 1b, 1c, 1d |
| **1f** | 四种任务类型 — 配置表单 + 执行逻辑 + 触发规则 | 1e |
| **1g** | 数据闭环 — 信号记录 + 快照 + 后续追踪 | 1f |
| **2** | AI 分析层 — LLM 接入，截图+信号解读 | 1g |
| **3** | 数据沉淀增强 — PostgreSQL、统计面板、信号胜率 | 2 |
| **4** | 自动交易 — 交易所 API 下单、策略引擎 | 3 |

---

## 九、用户与认证

- 少量用户（几人），简单密码认证
- 无用户注册/管理系统
- Session-based 认证，与现有 ChartShot 方案一致

## 十、未来扩展点

- **AI 分析**：Phase 2 接入 LLM，对信号和截图做技术分析解读
- **数据库升级**：Phase 3 迁移至 PostgreSQL + TimescaleDB
- **量化基础**：outcomes 表积累后可统计指标胜率、回测策略
- **自动交易**：Phase 4 基于信号 + AI 判断自动下单

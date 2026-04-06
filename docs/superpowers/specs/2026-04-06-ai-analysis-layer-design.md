# Phase 2: AI 分析层设计文档

> 为 WoHub 平台接入 OpenAI 兼容 LLM API，实现信号 AI 解读、流式对话、策略 Prompt 管理，并将 AI 分析融入推送流程。

## 设计原则

- **纯技术分析**：AI 仅基于价格、量能、技术指标做分析，不引入消息面。
- **先推后补**：信号推送不等 AI，AI 分析完成后编辑消息追加。
- **策略可进化**：Prompt 以版本化方式管理，数据沉淀后半自动优化。
- **OpenAI 兼容**：支持任何 OpenAI API 格式的服务（OpenAI、Claude via proxy、本地模型等）。

---

## 一、架构

```
┌─────────────────────────────────────────────────┐
│                   Vue 3 前端                      │
│  ┌──────────┬──────────────────────────────────┐ │
│  │ AI 分析   │  信号分析界面（流式打字机 SSE）     │ │
│  │ 策略管理  │  Prompt 编辑器 + 版本切换          │ │
│  └──────────┴──────────────────────────────────┘ │
└──────────────────┬──────────────────────────────┘
                   │ REST + SSE
┌──────────────────▼──────────────────────────────┐
│              FastAPI 主服务                        │
│                                                   │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────┐ │
│  │ LLM Client  │  │ Context      │  │ Strategy│ │
│  │ (OpenAI API │  │ Builder      │  │ Manager │ │
│  │  流式 httpx)│  │ (信号+截图+  │  │ (Prompt │ │
│  │             │  │  历史+胜率)  │  │  CRUD)  │ │
│  └──────┬──────┘  └──────┬───────┘  └────┬────┘ │
│         │                │               │       │
│  ┌──────▼────────────────▼───────────────▼────┐ │
│  │  AI Analysis Action (executor 集成)          │ │
│  │  信号触发 → 构建上下文 → LLM 调用 → 编辑推送  │ │
│  └────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

---

## 二、LLM Client

### 接入方式
- OpenAI 兼容 API（`/v1/chat/completions`）
- 配置项：API Key、Base URL、Model 名称、Max Tokens
- 存储在 `ai_config` 表（key-value 形式）

### 流式支持
- 使用 httpx 发起流式请求（`stream=True`）
- 后端通过 FastAPI 的 `StreamingResponse` + Server-Sent Events (SSE) 将 token 逐个传给前端
- 前端通过 `EventSource` 或 `fetch` + `ReadableStream` 实现打字机效果

### Vision 支持
- 当分析包含截图时，将图片转为 base64，以 OpenAI vision 格式传入 messages：
  ```json
  {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
  ```
- 如果模型不支持 vision，退化为纯文字分析

---

## 三、上下文构建器

AI 分析一个信号时，自动收集以下数据构建 LLM 上下文：

| 数据 | 来源 | 用途 |
|------|------|------|
| 信号信息 | signals 表 | 币种、指标、时间周期、触发时间 |
| 市场快照 | snapshots 表 | 触发时价格、成交额、涨跌幅、资金费率 |
| 历史信号 | signals 表（同币种同指标最近 10 条） | 该指标对该币种的历史触发频率 |
| 历史胜率 | outcomes 表 | 过去触发后 1h/4h/24h 涨跌统计 |
| 截图 | screenshots 表 / ChartShot 服务 | K 线图视觉分析（vision） |
| 当前策略 | strategies 表（is_default=1） | system prompt |

构建后的 messages 格式：
```python
[
    {"role": "system", "content": strategy.system_prompt},
    {"role": "user", "content": [
        {"type": "text", "text": "结构化信号数据 + 历史统计"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},  # 如有截图
    ]},
]
```

---

## 四、策略 Prompt 管理

### 数据模型
- `strategies` 表：id, name, system_prompt, is_default, created_at, updated_at
- 支持多个策略版本，但只有一个 is_default
- 内置一个默认策略（首次启动时自动创建）

### 默认策略内容
```
你是一个专业的加密货币技术分析师。你只基于价格走势、成交量、技术指标进行分析，不考虑任何消息面因素。

你的分析应该包括：
1. 当前信号的含义解读
2. 结合历史数据的可靠性评估
3. 关键支撑位和阻力位（如果截图可见）
4. 短期方向判断（看涨/看跌/中性）
5. 风险提示

保持简洁，每次分析控制在 200 字以内。使用中文回复。
```

### 管理功能
- CRUD：创建、编辑、删除策略
- 设为默认：切换当前使用的策略
- 版本记录：每次编辑保存为新记录（可回溯）

---

## 五、AI 分析动作（executor 集成）

### 执行流程

```
信号触发 → 推送文字+截图（即时）
        → 异步启动 AI 分析：
          1. 等截图完成（如有）
          2. 构建上下文
          3. 调用 LLM（非流式，等完整结果）
          4. 保存到 ai_analyses 表
          5. 编辑 Telegram/Discord 消息，追加 AI 解读
```

### 推送编辑格式
原消息：
```
🔔 信号触发 [04-06 12:34 UTC]
  BTCUSDT → 顶底背离 · 超卖
共 1 个标的
```

编辑后：
```
🔔 信号触发 [04-06 12:34 UTC]
  BTCUSDT → 顶底背离 · 超卖
共 1 个标的

🤖 AI 分析：
RSI 底背离出现在 4h 级别，同时价格触及前低支撑 62800 附近。
历史数据显示该指标在 BTC 上 24h 胜率 62%。
短期偏看涨，关注 64500 阻力。
⚠️ 若跌破 62000 则信号失效。
```

---

## 六、前端页面

### AI 分析页面（新增侧边栏入口 `/ai`）

**布局：**
- 左侧：最近信号列表（从 signals 表拉取，按时间倒序）
- 右侧：选中信号的 AI 分析详情
  - 信号基本信息卡片（币种、指标、时间、快照数据）
  - 截图展示（如有）
  - AI 分析内容（已有分析直接展示，无分析显示"生成分析"按钮）
  - 点击"生成分析"→ SSE 流式接收 → 打字机效果渲染
  - "重新分析"按钮（重新调用 LLM）

### 策略管理（Settings 页面新增区域）

- 策略列表（名称 + 是否默认）
- 编辑 Prompt（大文本框）
- "设为默认"按钮
- 新建/删除策略

### Settings 页面新增 AI 配置区

- API Key（密码输入框）
- Base URL（如 `https://api.openai.com/v1`）
- Model 名称（如 `gpt-4o`）
- Max Tokens（数字输入）
- 测试连接按钮

---

## 七、数据库变更

```sql
-- AI 配置（key-value 存储）
CREATE TABLE IF NOT EXISTS ai_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 策略 Prompt
CREATE TABLE IF NOT EXISTS strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- AI 分析记录
CREATE TABLE IF NOT EXISTS ai_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id),
    strategy_id INTEGER REFERENCES strategies(id),
    analysis_text TEXT NOT NULL,
    sentiment TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

---

## 八、API 端点

### AI 配置
| Method | Route | 说明 |
|--------|-------|------|
| GET | `/api/ai/config` | 获取 AI 配置（key 隐藏） |
| PUT | `/api/ai/config` | 更新 AI 配置 |
| POST | `/api/ai/test` | 测试 AI 连接 |

### AI 分析
| Method | Route | 说明 |
|--------|-------|------|
| GET | `/api/ai/signals` | 获取最近信号列表（带分析状态） |
| GET | `/api/ai/signals/{id}` | 获取信号详情 + 快照 + 历史 + 分析 |
| POST | `/api/ai/analyze/{signal_id}` | 触发 AI 分析（SSE 流式响应） |

### 策略管理
| Method | Route | 说明 |
|--------|-------|------|
| GET | `/api/ai/strategies` | 列出所有策略 |
| POST | `/api/ai/strategies` | 创建策略 |
| PUT | `/api/ai/strategies/{id}` | 编辑策略 |
| DELETE | `/api/ai/strategies/{id}` | 删除策略 |
| POST | `/api/ai/strategies/{id}/default` | 设为默认 |

---

## 九、前端路由变更

新增侧边栏入口：
```js
{ path: '/ai', component: AI, label: '信号分析' }
```

Settings 页面新增两个区域：
- AI 配置（API Key / Base URL / Model）
- 策略管理（Prompt 编辑器）

---

## 十、分阶段实施

| 阶段 | 内容 | 依赖 |
|------|------|------|
| **2a-1** | 数据库新增 3 表 + AI 配置 API + Settings AI 配置区 | 无 |
| **2a-2** | LLM Client（流式 httpx）+ 上下文构建器 | 2a-1 |
| **2a-3** | AI 分析 API（SSE 流式）+ executor 集成 + 推送编辑 | 2a-2 |
| **2a-4** | AI 分析前端页面（信号列表 + 流式打字机） | 2a-3 |
| **2a-5** | 策略 Prompt 管理（CRUD + 编辑器 + 默认策略） | 2a-1 |
| **2b** | 效果追踪（AI sentiment vs outcomes 对比） | 2a-3 + 数据沉淀 |

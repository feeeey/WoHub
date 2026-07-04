# 对话式 Agent（Chat Agent）— 设计

日期：2026-07-04
状态：已批准（用户确认：彻底替换批量裁决形态、SSE 全流式、多会话持久化、
交易侧只读工具、核心六件套指标、识图双入口、独立视觉模型槽位、
语义档案 DB 可编辑、执行架构选后台执行 + 事件溯源）
取代：`2026-07-02-agent-decision-layer-design.md` 的**产品形态**（批量裁决 +
复盘页 + 规则基线对比）。其基础设施（LLM 工厂、Fernet 配置、只读工具、
outcome 追踪、worker 模式）被本设计继承改造。

## 0. 定位与背景

上一版 agent 层把 LLM 做成"定时任务的批量信号裁决器"，与用户本意不符。
用户要的是 **Manus/OpenClaw 式的网页对话 agent**：在对话里向它提问，
实时看到它调用工具（跑筛选器、拉 K 线、算指标、截图看盘）的过程与结果，
人来叠加判断。三个被点名的缺口：

1. 配置页没有模型列表拉取、没有 key 有效性测试；
2. 批量裁决 + 复盘页不是用户想要的交互形态；
3. **筛选器（项目基石）的语义从未配置进项目**——每个指标什么含义、
   看多看空、怎么用，agent 无从知晓。

继承不变的约束：**纯技术分析**（无新闻/情绪/链上数据源）、**以损定仓**
（agent 不碰风险计算，仓位只来自 position_plan）、**agent 永不下单**。

## 1. 已定决策

| 问题 | 决定 |
|---|---|
| 旧批量裁决层 | 彻底替换：删 worker/队列/批量 decider/复盘页/agent_decide 动作/基线落库；保留 RuleDecider 阈值逻辑（任务管线本体）、outcome 追踪、LLM/配置基建 |
| 实时形态 | SSE 全流式：文字逐字输出 + 工具调用卡片实时弹出 |
| 会话模型 | 多会话 + SQLite 持久化（含完整工具轨迹），侧栏列表可新建/重命名/删除 |
| 交易侧工具 | 仓位规划预览 + 账户只读（持仓/挂单/余额）；红线：永不下单/改单/撤单 |
| 基础指标 | 核心六件套：MA/EMA、MACD、RSI、BOLL、ATR（复用已有）、量均/量比；接口可扩展 |
| 识图入口 | 双入口：agent 主动调用 ChartShot 截图工具 + 用户粘贴/上传图片 |
| 视觉能力 | 独立视觉模型槽位（同 provider/key、不同模型 id）；未配置时识图功能自动隐藏 |
| 筛选器语义 | DB 存储 + Settings UI 可编辑；初稿由 Claude 起草、用户审校 |
| 执行架构 | 后台执行 + 事件溯源：turn 在 worker 线程跑，事件落库，SSE 只是观察窗口，断线/刷新随时续播 |

## 2. 架构

```
发消息  POST /api/chat/sessions/{id}/messages   （文本 + 可选图片）
   → user 消息落库，创建 chat_turns(status='queued')，立即返回 turn_id
   → ChatWorker 后台 daemon 线程（lifespan 启停，单线程串行队列）领取 turn
        PydanticAI agent.iter 驱动工具循环
        事件边产生边写 chat_events：
          text_delta（~100ms 聚合一批，不逐 token 写库）
          tool_start / tool_progress / tool_end / image / turn_done / turn_error / cancelled
   → 前端  GET /api/chat/sessions/{id}/stream?after={last_event_id}
        （SSE / EventSource，cookie 认证，15s 心跳注释防代理断连）
        先补发 after 之后的积压事件 → 转实时跟播（进程内存订阅快路径，DB 兜底）
   → turn 结束：assistant 消息整体落库（全文 + trace_json + token 用量 + 模型名）
停止  POST /api/chat/turns/{id}/cancel → 置 cancel_requested，工具循环每步自查
```

- 并发模型：单用户系统，worker 一次执行一个 turn，多会话同时提问自然排队。
- 重启语义：启动时 `queued` 的 turn 保留继续执行（队列本就持久）；
  被打断的 `running` turn 标记 `failed`（error='服务重启中断'），前端显示重试按钮。
- 取消检查点：每次工具调用前 + 每个流式块之间。

## 3. 数据模型

沿用 SCHEMA 常量追加新表（CREATE TABLE IF NOT EXISTS）+ 索引；
`agent_config` 加列走 init_db 内幂等 ALTER TABLE 迁移（SCHEMA 追加对已建表是静默 no-op）。

```sql
chat_sessions(id, title TEXT, created_at, updated_at)
  -- title 自动取首条 user 消息截断，可重命名

chat_messages(id, session_id → chat_sessions, role TEXT 'user'|'assistant',
              content TEXT, images_json TEXT,     -- 图片相对路径列表
              trace_json TEXT,                    -- assistant：完整工具轨迹（历史回放）
              input_tokens INTEGER, output_tokens INTEGER,
              model TEXT, error TEXT, created_at)
  INDEX (session_id, id)

chat_turns(id, session_id, user_message_id → chat_messages,
           status TEXT 'queued'|'running'|'done'|'failed'|'cancelled',
           cancel_requested INTEGER DEFAULT 0, created_at, finished_at)
  INDEX (status)

chat_events(id, turn_id → chat_turns, seq INTEGER, type TEXT,
            payload_json TEXT, created_at)
  INDEX (turn_id, seq)

screener_semantics(key TEXT PRIMARY KEY,  -- 'oscillator/divergence_bottom' 等
                   meaning TEXT,   -- 这个筛选器在找什么
                   bias TEXT,      -- 方向倾向：long/short/中性/双向
                   usage TEXT,     -- 适用场景与用法
                   caveats TEXT,   -- 局限与常见假信号
                   combos TEXT,    -- 建议叠加的其他信号/周期
                   updated_at)
```

- `agent_config` 变更：新增 `vision_model TEXT`；`cooldown_minutes` 弃用（列保留不读）；
  `max_tool_calls`、`deep_dive_limit` 语义改为**每轮（turn）**上限。
- 图片存储：用户上传 → `data/chat_uploads/`（png/jpg，≤5MB，文件名随机化）；
  ChartShot 截图 → 沿用 `data/screenshots/`。消息只存相对路径，
  经登录保护端点 `GET /api/chat/images/{...}` 读取。
- 旧表 `agent_runs`/`agent_decisions` 保留但不再写入（避免迁移风险，不复用）。

## 4. 后端组件

新包 `backend/agent/chat/`：

| 模块 | 职责 |
|---|---|
| `runtime.py` | 单轮执行器：组装 system prompt、构建 PydanticAI Agent、`agent.iter` 循环、写事件、检查取消、落 assistant 消息 |
| `events.py` | 事件追加/按 seq 读取 + 进程内订阅分发（SSE 快路径），断线重连走 DB 补发 |
| `worker.py` | 队列 daemon 线程（替换旧批量 worker）：领取 queued turn 串行执行，stop event + join 停机 |
| `store.py` | 会话/消息 CRUD、标题生成 |
| `vision.py` | 视觉中继：图片 → 视觉模型 → 结构化盘面描述文本 |

### system prompt 组装（每 turn 动态）

1. 角色与红线（只读研究助手、纯技术分析、不下单、以损定仓提醒）；
2. **筛选器语义档案**：从 `screener_semantics` 全量读出渲染（这是 agent
   "理解基石筛选器"的机制，也是上一版缺失的核心）；
3. 工具使用指引（扫描是长任务省着用、kline 摘要与原始数据的选择、
   空结果双义性——`run_screener` 返回空可能是无信号也可能是 cookie 过期）；
4. 当前时间与市场会话上下文。

### 上下文管理

发给模型的历史 = 该会话最近 20 条消息的正文（user 原文 + assistant 最终文本）；
**工具轨迹不回灌**；图片仅在其所属 turn 内传递。超限从最旧截断。

### 工具清单（全只读）

| 工具 | 来源 | 说明 |
|---|---|---|
| `list_watchlists()` | 已有 fetch_watchlists | 扫描的前置 |
| `run_screener_scan(screeners, timeframes, watchlist_id)` | 已有 run_screener | **核心**。逐 combo 执行，每 combo 完成发 `tool_progress` 事件（前端卡片显示"3/9 完成 · 底背离@1h 命中 4"）；返回命中列表 + 交叉分析（复用 build_cross_analysis）。受全局 2s 限流 |
| `get_klines(symbol, interval, limit)` | 已有 fetcher | 紧凑数组 `[[t,o,h,l,c,v],…]`，limit≤300 |
| `get_indicators(symbol, interval, indicators)` | **新** `backend/klines/indicators.py` | 六件套数值：近段序列 + 当前状态摘要（如"MACD 零轴下金叉"）；纯本地计算 |
| `get_kline_structure(symbol, interval)` | 已有 kline_summary | 形态/分类/枢轴/ATR 摘要；占每轮 deep_dive 预算 |
| `get_market_snapshot(symbols)` | 已有 | 价格/涨跌/成交额/资金费 |
| `get_market_overview()` | 已有 gainers/losers/funding | 涨跌榜 + 资金费概览 |
| `get_signal_history(symbol, indicator)` | 已有 | 历史信号 1h/4h/24h 胜率（依赖保留的 outcome 追踪） |
| `capture_chart(symbol, interval)` | ChartShot + vision.py | 截图 → 存文件 → `image` 事件推前端内联显示 → 视觉模型分析 → 描述文本返回主模型；占 deep_dive 预算；未配置视觉模型或 ChartShot 不可用时不注册 |
| `get_position_plan(symbol, interval, direction)` | 已有 | 绑定凭据时注册；结构止损/RR 预览 |
| `get_account_overview()` | **新**，包装既有 Binance 客户端读接口 | 绑定凭据时注册（与 get_position_plan 同用 `agent_config.credential_id`）；持仓/挂单/余额只读 |

节流与预算：TradingView 全局 2s 锁、fapi ≥250ms 已有；每轮 `max_tool_calls`
上限（UsageLimits）；`deep_dive_limit` 约束 kline_structure + capture_chart 合计；
单条工具结果超 2000 字符截断（trace 与回传模型同规则）。

### 图片路径（识图双入口）

- agent 主动：`capture_chart` 工具（上表）。
- 用户上传：消息带图时——若**未配置** `vision_model`，直接把图片作为多模态
  内容传主模型（主模型不支持图像时该轮报错，提示配置视觉模型）；
  若**已配置** `vision_model`，一律先经 `vision.py` 中继成结构化描述再进主模型
  上下文（规则确定，无运行时探测）。

### 配置增强 API

- `GET /api/agent/models`：代理 provider 模型列表（OpenAI 兼容 `{base_url}/models`、
  Anthropic `/v1/models`），失败返回明确错误（key 无效/网络不通）。
- `POST /api/agent/test`：用当前表单值（未保存也可测）分别对主模型、视觉模型
  发最小请求；视觉模型额外带 1px 测试图验证图像输入；返回逐项可用性。

## 5. 前端

### 对话页（`/agent` 路由重造，导航名"Agent"不变）

- 左侧会话列表（新建/重命名/删除，按更新时间排序）；右侧消息流 + 输入区。
- 消息渲染：user 消息含图片缩略；assistant 文本 markdown 渲染、逐字流式追加。
- **工具调用卡片**：`tool_start` 弹卡片（spinner + 参数摘要）→ `tool_progress`
  更新进度行 → `tool_end` 定格结果摘要，点击展开完整参数/结果 JSON；
  `image` 事件内联图片卡片。
- 输入区：文本 + 图片粘贴/选择（png/jpg ≤5MB，无视觉模型且主模型未知时仍可发，
  错误由后端报回）；turn 进行中显示停止按钮。
- SSE：EventSource + `after` 序号续播；断线指数退避自动重连；
  刷新页面 = 加载历史消息 + 若有进行中 turn 则从事件表补齐再跟播。
- 复用既有风格：页头/主题变量/卡片样式（Tasks/Trade 页模式）。

### Settings 改造

- Agent 配置区：主模型/视觉模型改为"下拉（来自 /api/agent/models）+ 可手输"；
  「测试连接」按钮逐项显示结果；删除 cooldown 输入；其余字段沿用。
- 新增「筛选器语义」子区：每个筛选器一张可编辑卡片
  （meaning/bias/usage/caveats/combos 五个字段），保存写 `screener_semantics`。

## 6. 旧层移除与保留

**删除**：`agent/worker.py`（旧）、`agent/queue.py`、`agent/agent_decider.py`、
`agent/prompts.py`（批量版，chat 的 prompt 随 runtime 新写）、
API 的 runs/decisions/rerun/rate/stats 端点、`frontend/src/views/Agent.vue`（复盘页）、
`agent_decide` 任务动作、executor 中的入队调用与 `record_rule_run` 基线落库、
相关旧测试（test_agent_worker/queue/decider/enqueue/api/stats/store/schema 等按实际归属清理）。

**保留**：`agent/decider.py` 的 RuleDecider（任务管线的推送阈值逻辑本体）、
outcome 追踪全套（tracker/outcome_poller/outcome_checks，signal_history 工具依赖）、
`agent/config.py`（扩展）、`agent/llm.py`（扩展）、`agent/tools.py`（重构扩展）、
`agent/validator.py` stub、表 agent_runs/agent_decisions（休眠）。

## 7. 红线（不变 + 新增）

- ❌ agent 永不下单/改单/撤单；`backend/agent/` 及 import 链禁止出现下单函数。
- ✅ 想执行 → assistant 文字里给 Trade 页预填链接（symbol/direction query 参数，机制已存在）。
- ✅ 工具全只读、自带节流；每轮工具调用与深评上限。
- ✅ LLM api_key Fernet 加密存储；图片端点受登录保护。
- ✅ 完整审计：每条 assistant 消息带 trace_json（工具轨迹/token/模型）。
- ✅ worker 与调度管线解耦；LLM 延迟不占 APScheduler 线程池。
- ✅ 纯技术分析：不注册任何新闻/情绪/链上工具。

## 8. 错误处理

- LLM 调用失败：turn → failed + error 落库，`turn_error` 事件；前端该消息位
  显示错误与「重试」（同一条 user 消息重新入队新 turn）。
- 工具单次失败不终止轮次：错误说明作为工具结果返回模型（面向 LLM 撰写），
  由它决定重试或绕过；连续失败受 max_tool_calls 兜底。
- ChartShot/TradingView cookie 失效：工具结果里明确告知"服务不可用/需更新 cookie"。
- SQLite 单写者：事件写入短事务；text_delta ~100ms 聚合；worker 自有连接。
- SSE 连接数：单用户场景不做连接池管理；每会话页同时保持一条流。

## 9. 测试策略

- runtime：PydanticAI `TestModel`/`FunctionModel` 无网测试——事件序列完整性
  （start→delta→tool→done 顺序与 seq 单调）、取消中断、失败落库、上下文裁剪。
- 事件续播：写入 N 条后从任意 after 读，断言不重不漏；重启恢复语义（queued 续跑、running 判 failed）。
- indicators：对已知 K 线序列断言 MA/EMA/MACD/RSI/BOLL/量比数值（金标准用例）。
- 工具层：每轮预算耗尽行为、节流间隔、超长截断、scan 进度事件条数 = combo 数。
- API：会话 CRUD、消息发送、SSE 流（httpx 流式读断言 event 帧）、上传校验
  （类型/大小/路径穿越）、models 代理错误透传、test 端点。
- 真网调用标 `pytest.mark.network`。
- 语义档案：初稿以 seed 数据入库（8 个筛选器），用户 UI 审校后生效。

## 10. 交付切分

1. 数据层 + 事件基建（表/迁移/events.py/store.py + 测试）
2. 工具层重构 + `klines/indicators.py`（六件套 + 金标准测试）
3. runtime + worker + SSE 端点（TestModel 全链路无网测试）
4. 对话页前端（会话列表/消息流/工具卡片/停止/续播）
5. 视觉中继 + capture_chart + 图片上传
6. 配置增强（models 代理/test 端点/Settings 改造）+ 语义编辑 UI + 语义初稿 seed
7. 旧层移除收尾 + CLAUDE.md 更新

每阶段可独立验证；旧层移除放最后，此前新旧并存互不干扰（旧层无 UI 入口即等效下线）。

## 11. 显式不做

- 不做自动执行/自动下单（永久红线，同前设计）。
- 不做多用户/多租户（单密码系统）。
- 不做消息编辑、分支重生成、会话分享导出。
- 不做 agent 主动发起的定时对话/盯盘播报（未来可基于本事件基建另立设计）。
- 不引入新闻/情绪/链上数据源（纯 TA 约束）。
- 不迁 Postgres、不引入消息队列中间件（DB 即队列，单 worker）。
- 不在本轮实现 StrategyValidator（接口 stub 继续保留给量化层 P3）。

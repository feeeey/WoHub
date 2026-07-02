# Agent 决策层 — 设计

日期：2026-07-02
状态：已批准（用户确认 Phase 0-4 范围、PydanticAI 运行时、双协议 LLM、批入口逐信号裁决、验证器只留接口）
取代：仓库根目录 `WoHub-Agentification-Plan.md`（与 Opus 的讨论稿，其架构论断已对照代码重新评估）

## 0. 定位与核心结论

把 LLM 决策层（"大脑"）插入现有信号管线的"决策缝"，只做**研究/决策支持**，
绝不自动下单。原讨论稿的核心判断经代码验证后成立，但有四处修正：

1. **PineForge 不存在**。离线验证器角色本轮只定义 `StrategyValidator` 接口与数据契约，
   实现留给量化层 P3（`docs/superpowers/specs/2026-06-11-quant-factor-layer-design.md`）或外部项目。
2. **本仓库刚删除过一版 LLM 功能**（commit 4246f3b，2026-06-20 减法重构）：自由文本点评 +
   关键词猜 sentiment、输出无人消费、设计中的"AI 观点 vs outcomes 对比"从未实现即被删。
   本设计与之的本质区别：结构化裁决 schema、决策↔结果闭环第一天就建、有 RuleDecider 基线可证伪。
3. **tracker 不是"现成的实盘验证器"**：内存 `threading.Timer` 重启即丢
   （tracker.py:57-64）、快照 price=0 永久静默跳过（tracker.py:86-88）。闭环建立之前必须先加固，
   否则决策打分建立在系统性删失的数据上。
4. **决策 schema 对齐量化层 P4** 已定义的
   `{symbol, interval, direction: long|short, confidence: 0-1, factors}`——未来 auto_trade
   消费端对"规则因子"与"LLM"两种生产者无差别，不分叉管道。

用户交易方法约束继承自量化层设计：**纯技术分析**（不引入新闻/情绪/链上数据源）、
**以损定仓**（仓位永远由 `position_plan.py` 反推，agent 不碰风险计算）。

## 1. 已定决策（原讨论稿 §8 开放问题的答案）

| 开放问题 | 决定 |
|---|---|
| 决策粒度 | 批入口、逐信号裁决：Decider 接收一次任务运行的完整批（含交叉分析），输出对每个候选信号的独立裁决 |
| 输出形态 | 结构化决策对象写库 + 前端复盘页；推送为可选跟随消息 |
| 验证器连接 | 本轮只留 `StrategyValidator` 接口 + 数据契约文档 |
| 运行时 | PydanticAI（`pydantic-ai-slim[openai,anthropic]`，控制依赖面）；双协议：OpenAI-compatible（可配 base_url）+ Anthropic |
| SQLite 是否够用 | 够。沿用 get_db 连接每调用 + WAL + 短事务；worker 用自有连接。不迁 Postgres |
| 实现范围 | Phase 0-4（到闭环）。Phase 5（受限自动执行）永久移出本设计，前置条件见量化层 P4 |

## 2. 架构

```
executor 任务运行（调度器 max_workers=1 单线程，不动）
  └─ 筛选 → 交叉分析 → Decider.decide(SignalBatch)
       ├─ RuleDecider：现有阈值逻辑，零行为变化；决策落库 = 基线
       └─ 推送/落库信号（现有流程不变）
  └─ 任务 actions 含 "agent_decide" 且 agent 已启用：
       INSERT agent_runs(status='queued', context_json=批上下文快照) —— 不等待
                    ↓ 解耦（DB 队列，重启安全）
AgentWorker 后台 daemon 线程（lifespan 启停）
  └─ 轮询领取 queued run → PydanticAI Agent.run_sync
       ├─ 只读工具循环（节流 + 调用上限）
       └─ output_type 强制结构化 DecisionSet
  └─ 写 agent_decisions（逐信号裁决）+ trace_json 完整审计
  └─ 可选：sender.send_text 推送简短裁决（跟随消息，HTML 转义，channel 无关）
                    ↓
复盘页 /agent：runs → 决策明细（裁决/理由/工具轨迹）→ outcomes 回流打分
  → 人工评分 → AgentDecider vs RuleDecider 基线统计
  → "采纳" = 带参跳转 Trade 页预填 → 人工确认模态框 → 现有 bracket 管道
```

关键约束（评估确认为硬约束，非建议）：

- 调度器线程池 max_workers=1 是刻意设计（scheduler.py:33-39），LLM 调用**绝不内联**；
  旧版功能的"先推后补"原则与 daemon 线程 offload 是两处既有先例。
- 筛选器结果只有 symbol 字符串（pine_screener.py:222-244），decider 证据必须靠工具补；
  klines fapi 路径**无既有限流**（1req/2s 只管 TradingView），工具层必须自带节流。
- `run_screener` 返回 `[]` 无法区分"无信号"与"重试耗尽/cookie 过期"——批上下文照实传递，
  prompt 中告知 agent 空结果的双义性。

## 3. 数据模型

新表全部用 `agent_*` / `outcome_checks` 命名——已部署库中残留孤儿表
`ai_config/strategies/ai_analyses`（4246f3b 提交说明），不得复用旧名。
schema 走既有 SCHEMA 常量追加（CREATE TABLE IF NOT EXISTS 对新表安全）；新表附带索引（现库无任何索引）。

```sql
-- Phase 0：tracker 持久化（替换内存 Timer）
outcome_checks(id, signal_id → signals, horizon TEXT '1h'|'4h'|'24h',
               due_at TEXT, done INTEGER DEFAULT 0, error TEXT, created_at)
  INDEX (done, due_at)

-- Phase 1：LLM 配置（单行，id=1）
agent_config(id, provider TEXT 'openai'|'anthropic', base_url TEXT,
             api_key_enc TEXT,          -- Fernet（复用 trading/credentials.py helpers）
             model TEXT, max_tokens INTEGER,
             max_tool_calls INTEGER DEFAULT 15,
             deep_dive_limit INTEGER DEFAULT 5,   -- 每 run kline_summary 上限
             cooldown_minutes INTEGER DEFAULT 240, -- 同 symbol×timeframe 裁决复用窗口
             credential_id INTEGER,               -- 可空；指定后才注册 position_plan_preview 工具
             push_verdict INTEGER DEFAULT 0, enabled INTEGER DEFAULT 0, updated_at)

-- Phase 2：运行与裁决
agent_runs(id, task_id → tasks, decider TEXT 'agent'|'rule',
           status TEXT 'queued'|'running'|'done'|'failed',
           context_json TEXT,   -- SignalBatch 快照
           trace_json TEXT,     -- 工具调用轨迹（每条结果摘要截断 2000 字符）+ 推理摘要
           model TEXT, prompt_version TEXT,
           input_tokens INTEGER, output_tokens INTEGER,
           error TEXT, created_at, started_at, finished_at)
  INDEX (status), INDEX (task_id, created_at)

agent_decisions(id, run_id → agent_runs, signal_id → signals,  -- 代表信号（首行）
                signal_ids_json TEXT,   -- 同 symbol×timeframe 的全部关联信号 id
                symbol TEXT, timeframe TEXT,
                direction TEXT 'long'|'short'|'skip', confidence REAL,
                reasons TEXT, factors_json TEXT,   -- 对齐量化 P4 schema
                human_rating INTEGER,               -- NULL|1 好|0 坏|-1 存疑
                created_at)
  INDEX (symbol, timeframe, created_at), INDEX (run_id)
```

RuleDecider 基线也写 agent_runs（decider='rule'、trace 为空、零 token）+ agent_decisions：
direction 取自筛选器配置新增的可选 `bias` 字段（long/short；如 divergence_bottom→long）。
多筛选器 bias 一致时才写方向，否则 direction='skip' 且不参与方向感知统计。
基线落库时机：**每次产信号的任务运行**（watchlist_signal/market_scan）都写，
与任务是否开启 `agent_decide` 无关——Phase 0 起即累积基线样本，供 Phase 4 配对对比。

## 4. 分阶段交付

### Phase 0 — 决策缝 + 数据地基加固

1. `backend/agent/decider.py`：`Decider` 协议
   `decide(batch: SignalBatch) -> DecisionSet`。
   `SignalBatch`：task 元数据 + 逐 resolution 筛选结果 `[{label, resolution, symbols}]` +
   交叉分析（含目前无人消费的 `resolution_overlap`/`full_overlap`，pine_screener.py:279-315）+
   15s TTL ticker 快照。`RuleDecider` 包装现有阈值逻辑（executor.py:100-109 与 176-177），
   golden 测试对旧逻辑逐字节等价，**零行为变化**。
2. tracker 重启安全化：`_record_signals` 改写 `outcome_checks` 三行到期任务；
   独立轮询线程（60s 间隔，lifespan 启停）执行到期检查并 re-arm 启动时的存量；
   price=0 / ticker 缺失记 `error` 列而非静默跳过。删除 threading.Timer 路径。
   迁移前已存在的历史信号不回填 outcome_checks——接受为删失历史。
3. 修 `/api/tasks/{id}/test` 的 label×resolution 叉乘写库 bug（executor.py:308-314：
   手动测试运行会把每个 label 在所有 resolution 下重复入库并重复起 outcome 计时）。
4. `signals.indicator` 编码统一为纯 label（timeframe 已有独立列；watchlist 路径现存
   `"MACD金叉(1h)"` 双编码，executor.py:115）。历史查询侧对旧数据 LIKE 兼容。

交付：接缝就位、RuleDecider 基线落库、outcomes 数据重启安全。

### Phase 1 — Agent 基础设施

1. 依赖：`pydantic-ai-slim[openai,anthropic]`（唯一新增运行时依赖，偏离"手写客户端"
   房子风格是用户明确选择）。
2. `agent_config` 表 + `backend/api/agent.py` 配置端点（挂 protected router）+
   Settings 页"Agent 配置"区。api_key 用 `trading/credentials.py` 的 Fernet helpers 加密；
   SECRET_KEY 为默认值时允许保存但显式警告（沿用 insecure_defaults 模式；
   注意：轮换 SECRET_KEY 会作废已存 key，与交易凭据同语义，文档标注）。
3. `backend/agent/tools.py` 只读工具（全部纯读、带全局节流，fapi 调用间隔 ≥250ms）：
   - `market_snapshot(symbols)` — 复用 15s TTL 缓存的 ticker/funding 聚合（近乎免费）
   - `kline_summary(symbol, interval, n)` — 压缩摘要：近 N 根 OHLCV 统计 + 16 形态命中 +
     4 级分类 + fractal pivot + ATR。**不返回原始蜡烛数组**（1500 根 dict 会撑爆 token）。
     每 run 调用次数受 `deep_dive_limit` 约束。
   - `signal_history(symbol, indicator)` — 同 symbol×indicator 历史信号 + outcomes 胜率
     （Phase 0 修好编码后才可靠；沿用 api/tasks.py:241-249 的 join 模式）
   - `position_plan_preview(symbol, interval, direction)` — 可选：仅当 agent_config 指定了
     专用凭据 id 时注册（build_position_plan 需凭据拉 equity/exchangeInfo；无凭据则不注册此工具）
   红线：注册表中**不存在**任何下单/撤单/kill-switch 函数；`backend/agent/` 不 import
   `place_order_bracket`/`close_all`。
4. 工具描述与报错信息面向 LLM 撰写（质量的一半藏在这里）。

交付：可配置、可测试的 LLM 客户端与工具层（PydanticAI `TestModel` 无网测试）。

### Phase 2 — AgentDecider worker

1. `backend/agent/worker.py`：daemon 线程，lifespan 启停（stop event + join(10s)，
   不用旧版 fire-and-forget）；每 2s 轮询
   `UPDATE agent_runs SET status='running' ... WHERE status='queued'` 原子领取；
   自有短事务连接，绝不触碰调度器线程池。
2. 入队点：executor `_record_signals` 之后、任务 actions 含 `"agent_decide"` 且
   agent_config.enabled 时插入 queued run。executor 不等待、不感知结果（先推后补）。
3. `AgentDecider`（PydanticAI Agent，`run_sync`）：
   - prompt：SignalBatch 紧凑渲染 + 空结果双义性告知 + 纯技术分析约束 + PROMPT_VERSION 常量
   - 工具循环上限 `max_tool_calls`；`output_type=DecisionSet`（逐信号
     `{symbol, timeframe, direction: long|short|skip, confidence, reasons, factors}`）
   - 冷却去重：同 symbol×timeframe 在 `cooldown_minutes` 内已有 agent 裁决则复用——
     **不新写 decision 行**，trace_json 记录"复用裁决 #id"；统计侧每条裁决只计一次
4. 审计：trace_json 记录每次工具调用（name/args/结果摘要）+ token 用量 + model + prompt 版本。
5. 失败处理：status='failed' + error；不自动重试（复盘页提供手动重跑=重新入队同 context）。
6. 可选裁决推送：run 完成后经 `channels/sender.send_text` 发跟随消息（channel 无关；
   HTML 转义防 Telegram parse_mode 崩溃；不做消息编辑——message_id 从未持久化，Discord 无编辑能力）。

交付：agent 对信号批产出带理由、带完整审计的结构化裁决。

### Phase 3 — 复盘页

1. `/agent` 路由 + App.vue navItems（两处都要加）+ api client 方法
   （listAgentRuns/getAgentRun/rateDecision/getAgentStats/getAgentConfig/updateAgentConfig）。
2. runs 列表（时间/task/decider/状态/token/裁决数）→ 明细：逐裁决卡片
   （direction/confidence/reasons/关联信号）+ 工具轨迹折叠面板。
3. 人工评分（好/坏/存疑）写 human_rating。
4. outcomes 列：join 显示 1h/4h/24h，**方向感知染色**（long×涨=绿；short×跌=绿）——
   裁决自带 direction，绕开 signals 方向盲问题。
5. "采纳"按钮 = 带 query 参数跳转 Trade 页（symbol/direction 预填），走现有
   compute-plan → 确认模态框 → bracket 管道。**服务端无任何 agent 触发的下单路径。**
6. 复用既有模式：history-table（Tasks.vue）、countdown 轮询、页头/主题变量；
   fetch wrapper 无超时，所有 agent 端点必须是"触发+轮询"形态，不长挂。

交付：能回答"它当时凭什么这么判断"，且每条裁决有事后胜负。

### Phase 4 — 闭环量化 + 验证器接口

1. `GET /api/agent/stats`：按 decider（rule/agent）、confidence 桶（<0.5 / 0.5-0.7 / ≥0.7）、
   timeframe、horizon 聚合的方向感知胜率与平均收益；样本不足（<20）的格子显式标注不可信
   （沿用量化层 P1 的纪律）。
2. 复盘页统计卡片：AgentDecider vs RuleDecider 基线对比——旧版 AI 功能无基线故不可证伪，
   这是本设计的核心防线。
3. `backend/agent/validator.py`：`StrategyValidator` 协议 stub
   （`validate(strategy_spec) -> ValidationReport`）+ 数据契约文档：任何要"上升为策略"的
   逻辑（prompt 固化、阈值规则）必须先过验证器（未来 = 量化层 P3 walk-forward 或外部项目），
   多重检验校正的责任在验证器实现方。本轮只留接口。

交付：agent 决策质量可量化、可与基线对比；验证器缝隙已定义。

## 5. 红线（任何阶段不破）

- ❌ LLM agent 不接自动下单；`backend/agent/` 不 import 任何下单函数。
- ✅ 决策 → 人工确认（Trade 页既有模态框）→ 确定性服务执行。
- ✅ 每条裁决完整审计：prompt 版本 / 工具轨迹 / 理由 / token / 事后 outcome。
- ✅ agent 工具全只读且自带节流；每 run 有工具调用与深评上限。
- ✅ worker 与调度管线解耦（DB 队列 + 独立线程），LLM 延迟不占 max_workers=1 线程池。
- ✅ LLM api_key Fernet 加密存储（不重蹈旧版 ai_config 明文）。
- ✅ agent 不碰风险计算，仓位只来自既有 position_plan。
- ✅ 策略化逻辑上线前过 StrategyValidator（本轮接口，实现在量化层 P3/外部）。

## 6. 测试策略

- RuleDecider golden 等价测试：对同一输入，重构前后推送集合与落库信号逐项一致。
- worker：PydanticAI `TestModel`/`FunctionModel` 无网络单测（队列领取、冷却去重、
  失败落 error、trace 写入）；网络真调标 `pytest.mark.network`。
- 工具层：节流生效、deep_dive_limit 截断、kline_summary 输出形状。
- tracker：重启 re-arm（插入到期行 → 新进程扫描补算）、price=0 记 error。
- API/前端：沿用 conftest（AsyncClient + reset_db + auth_override）。

## 7. 显式不做

- 不做自动执行（原 Phase 5 移出；前置条件按量化层 P4：kill-switch + 日亏熔断 + dry-run 先行）。
- 不做消息编辑式裁决追加（message_id 无持久化、Discord 无编辑；用跟随消息）。
- 不做 anomaly_watch/scheduled_shot 的 agent 化（前者不落库信号，需先补持久化，另立后续）。
- 不做多 agent 编排/复杂分支（单 decider；PydanticAI 已够）。
- 不迁 Postgres、不引入队列中间件（DB 即队列，单 worker）。
- 不修 push_logs 的既有缺陷（channel_id 恒 NULL、假 success）——与本层无依赖，另立技债。

# 安全加固与静默失败治理 — 升级文档

日期：2026-07-27
状态：已完成，测试 543 → 625（+82），全绿
关联：`2026-05-31-api-auth-enforcement-design.md`（鉴权基线）、
`2026-06-07-secure-defaults-testnet-verify-design.md`（不安全默认值门禁）、
`2026-07-26-agent-measurement-upgrade.md`（后验闭环与验证器的上一版）

## 0. 出发点

这轮不是加功能，是一次全项目审计后的收口。审计覆盖 11 个子系统（核心基础设施、
API 层、数据源、任务调度、chat agent、学习/测量层、交易、K线分析、截图链路、
前端、部署面），每条结论都要求代码证据，关键结论另做对抗性复核。

审计的核心发现可以概括成一句话：**这个项目的"想清楚了的地方"质量很高，
问题几乎全部出在"没人会去看的地方"。**

具体而言，交易模块（clientOrderId 幂等 + 网络异常后查单确认 + 止损失败即撤销入场）
和 K线模块（严格区分已收盘/未收盘）是明显高于行业平均水准的工程；而下面 8 个缺陷，
有 7 个的共同特征是**故障时系统看起来完全正常**：没有报错、没有告警、UI 上一切正常，
只是结果悄悄变成了错的。

| 缺陷 | 故障时的表象 |
|---|---|
| 路径穿越 | 静态资源服务照常工作 |
| 会话可伪造 | 登录页照常显示 |
| 调度器漏跑 | 任务列表显示"已启用" |
| 筛选器限流 | 推送显示"0 命中" |
| 简评冷却串号 | 简评"按冷却策略跳过了" |
| p 值虚低 | 验证器给出"显著"结论 |
| 轮次卡死 | 对话"正在思考中" |
| 日志被冲掉 | 日志页面有内容 |
| 推送发失败 | push_logs 一片绿色的 success |

审计本身用 11 个子系统评审代理并行完成，关键结论再交由独立的对抗性验证代理
逐条试图推翻（23 个代理，无失败）。这个安排纠正了我自己的一处误判，见 §1.9。

## 1. 严重缺陷与修复

### 1.1 未授权任意文件读取（Critical，`backend/main.py`）

SPA 兜底路由把请求路径直接 `os.path.join` 到静态目录：

```python
@app.get("/{path:path}")
async def serve_spa(path: str):
    file_path = os.path.join(_static_dir, path)   # path 已被 URL 解码
    if os.path.isfile(file_path):
        return FileResponse(file_path)
```

Starlette 交给路由的 `path` 已经过百分号解码，`%2e%2e%2f` 到这里就是 `../`，
`os.path.join` 会老老实实走出静态根目录。该路由**没有任何鉴权依赖**。

已用真实 uvicorn 服务端复现（不是测试客户端）：

```
GET /%2e%2e%2fdata%2fwohub.db      → 整个 SQLite 库
GET /%2e%2e%2f%2e%2e%2f…%2fetc/passwd → root:x:0:0:root:/root:/bin/bash
GET /../secret_outside.txt          → 未编码形式同样有效
```

容器 `WORKDIR /app`、静态目录 `/app/static`、数据卷 `/app/data`，所以
`../data/wohub.db` 精确命中数据库；容器以 root 运行，可读范围是整个文件系统。

**为什么会漏掉**：`api/screenshots.py` 和 `api/chat.py` 的文件路由都做了
「文件名白名单 + realpath + commonpath 二次校验」，团队显然懂这件事。问题在于
这个路由**只在生产镜像里存在**——它挂载的前提是 `backend/static/` 目录存在，
而那个目录由 Dockerfile 的前端构建阶段产生，开发检出和 CI 里都没有。
测试根本碰不到这段代码。**这不是知识缺口，是测试可见性缺口。**

修复：
- 抽出 `resolve_static_file(static_dir, path)`：先 `realpath` 解析软链，再要求
  结果落在根目录内（沿用 `api/screenshots.py` 的既有校验风格）
- 抽出 `mount_spa(app, static_dir)`：**把静态目录变成参数**，测试就能在临时目录
  上挂载真实路由，闭掉上面那个可见性缺口
- 未匹配的 `/api/*` 返回 404 而不是回落 index.html——前端对每个 `/api` 响应都
  执行 `res.json()`，返回 HTML 会变成难以理解的解析错误

新增 `tests/test_spa_static.py`（14 项）：软链逃逸、`....//` 变形、编码与未编码
穿越、目录与缺失文件，其中 3 项通过完整 ASGI 栈（百分号解码正是在那里发生）。

### 1.2 默认 SECRET_KEY 下会话可离线伪造（Critical，`backend/auth.py`）

会话 cookie 用 `itsdangerous` 以 `SECRET_KEY` 签名，而 `docker-compose.yml` 的
默认值是公开的 `change-me-in-production`。任何人都能离线铸造：

```python
URLSafeTimedSerializer("change-me-in-production").dumps({"authenticated": True})
# → eyJhdXRoZW50aWNhdGVkIjp0cnVlfQ.…  直接带上就是已登录
```

原有防护只是启动时打一条告警，登录接口照常工作。

**修复的取舍**：最直接的做法是默认值时拒绝启动，但那会毁掉
`docker-compose up` 就能试用的上手体验；而把 SECRET_KEY 自动改成随机值又会
**连带换掉 Fernet 派生密钥，让已存的 API secret 全部解不开**。

因此把**签名密钥与加密密钥拆开**：

- 新增 `settings.session_secret`。`SECRET_KEY` 非默认时原样沿用（已正确配置的
  部署完全不受影响，现有会话继续有效）；仍是默认值时，改用
  `data/session_key` 里自动生成并持久化的随机密钥（0600，`O_EXCL` 独占创建）
- 凭据加密**继续**派生自 `SECRET_KEY`，所以已存的 API secret 一个都不会失效
- `insecure_defaults()` 的语义保持不变，主网门禁照旧——因为 Fernet 密钥确实还弱，
  该拦的还得拦

代价：默认配置的部署升级后需要重新登录一次。那些 cookie 本来就是可伪造的。

新增 `tests/test_session_key.py`（8 项），含直接用默认密钥伪造 cookie 并断言被拒。

### 1.3 调度器静默丢弃任务（Critical，`backend/tasks/scheduler.py`）

`CRON_TRIGGERS` 里每一个周期都落在相同的分钟标记上（`:58`，以及 `:13/:28/:43`）,
所以到点时所有到期任务被同时释放。而执行器只有一个工作线程：

```python
executors={"default": {"type": "threadpool", "max_workers": 1}},
job_defaults={"coalesce": True, "max_instances": 1},   # 没有 misfire_grace_time
```

APScheduler 的 `misfire_grace_time` **默认是 1 秒**（3.11.3 源码：
`asint(job_defaults.get("misfire_grace_time", 1))`）。任务排在队里超过 1 秒就
被直接丢弃，不是延后执行。而单个任务动辄跑几分钟（筛选器限流 1 次/2 秒，
截图约 10 秒/张）。

用项目原配置复现，三个同时到期的任务：

```
EXECUTIONS: [('start', 1, 2.0), ('end', 1, 6.0)]
=> 3 个任务里只有 1 个真的执行了
```

**只要配了两个以上的任务，除第一个之外全部每轮被静默丢弃。**
这直接击穿产品的核心价值（多任务信号聚合）。

更糟的是它不可见：APScheduler 只通过标准 `logging` 报告漏跑，而项目的
`app_logger` 是个独立的内存环形缓冲区，没有和 `logging` 打通——那些警告去了
stderr，`/api/settings/logs` 里一条都看不到。任务可以停跑几周而界面毫无异常。

修复：
- `misfire_grace_time = 3600`：让任务排队等待而不是被丢弃
- `_ApplogHandler` 把 `apscheduler` logger 的 WARNING+ 桥接进 `app_logger`，
  漏跑从此在 UI 可见

放宽 grace 会不会导致重启后一次性补跑一大堆？**不会**，已验证：调度器用的是默认
的 `MemoryJobStore`（无 `jobstores` 参数），任务不跨进程持久化——重启时由
`start_all_enabled()` 重新注册，下次触发时刻从当下重算，根本不存在积压。
这个 grace 窗口只作用于"在同一个进程里排在忙碌的 worker 后面"这一种情况，
也正是这个缺陷本身。

新增 `tests/test_scheduler_misfire.py`（4 项），核心用例断言三个同时到期、
第一个耗时的任务最终全部执行。

### 1.4 后验验证的独立性假设错误（High，`backend/agent/validator.py`）

`OutcomeValidator` 把每条 signal 行当作一次独立伯努利试验做二项检验。但真实
触发形态根本不独立：

- 一次扫描会在几十个高度相关的币种上同时命中（加密市场与 BTC 相关性极高）
- 条件持续期间，同一标的会在连续多根K线上重复触发

n 被虚增一个数量级，p 值随之虚低。用蒙特卡洛量化（模拟一个**毫无方向性优势**
的筛选器，40 批扫描 × 每批 30 个同涨同跌的相关标的，400 次重复）：

```
按原始行计数 -> 判为『显著』131 次 = 32.8%   (名义应为 2.5%)
按时间簇计数 -> 判为『显著』 14 次 =  3.5%
```

**假阳性率是名义值的 13 倍。**这个验证器的全部意义就是回答"这个筛选器可信吗"，
而它在三分之一的情况下会给纯噪声盖上"显著"的章。

修复：检验对象从原始信号行改为**时间簇**。按视界长度分桶（1h/4h/24h），
每个桶取平均收益算作一次试验；`min_samples` 作用于簇数；分段折检查也在簇序列上做。
同时报告 `n_rows` 与 `n`，让样本收缩对使用者可见。

顺带修掉一处不一致：原实现的二项检验用的是"上涨数"，而 `hit_rate` 用的是
"声明方向命中数"。当存在 `change == 0` 的样本时两者分母不同，做空声明的
p 值会算错。现在统一用声明方向的命中数。

回归用例用同一份数据展示两种算法的分歧：

| 算法 | n | 命中 | p 值 | 结论 |
|---|---|---|---|---|
| 按原始信号行 | 1200 | 720 | 4.4e-12 | 压倒性显著 |
| 按独立时段 | 40 | 24 | 0.268 | 不显著 |

`outcome_stats` 同步改造：注入 system prompt 的统计行现在写
`n=800 条 / 12 个独立时段`，独立时段少于 10 个时追加"结论脆弱"。
不标出来的话，模型（和人）会把 n=800 读成 800 次独立验证。

### 1.5 限流被当成"行情平静"（High，`backend/sources/pine_screener.py`）

重试耗尽后 `run_screener` 返回 `[]`，与"跑成功了但没有命中"完全同形。executor
把它当正常空结果，推送里显示"0 命中"。**用户看到的是"今天没机会"，实际是
"今天没查到"。**

修复：新增 `ScreenerUnavailable` 异常；executor 统一走 `_run_screeners()`，
把成功结果与失败明细分开返回。失败会：

- 写 `app_logger` 的 error
- 附加到推送消息尾部：`⚠️ 2 个筛选器本轮未取到结果：…（结果不完整，勿据此判断行情平静）`
- 全部失败时写一条 `status='failed'` 的 `push_logs`

这里有个连带效应值得写下来：叠加阈值（`overlap_threshold`）是按**实际返回**的
筛选器数量算的。三个筛选器里挂了一个，"2 个以上共振"这个条件就变得更难满足，
信号会莫名其妙变少——现在提示语会把这层告诉用户。

前端同步改造（`frontend/src/views/Tasks.vue`）。这一步不能省：任务测试面板只渲染
`results` / `total_signals` / `signals`，**根本不显示 `message`**，所以后端把警告写进
消息尾部在这个界面上完全看不见——修了一半等于没修。现在 `failures` 单独渲染，
并给"结果为真但不完整"单独一档 `.test-partial` 配色（`--warning`），不与"成功"
共用绿色。绿色勾配上不完整的结果，比不显示更糟。

顺带清掉 `_DEFAULT_COOKIES` 里硬编码的真实 `device_t` 会话令牌和 `_ga` 标识：
那是从某人浏览器里抄出来的凭据，进版本库就是泄漏，而且让所有部署共用同一个
TradingView 设备身份（一处被限流，处处被限流）。UI 上本来就写着"Pine 指标筛选
需要 TradingView 登录态"，登录态一律由运营者提供；没配置时现在会明确告警，
因为 TradingView 对未登录请求返回的是空结果而不是 401——又一个静默失败。

### 1.6 AI 简评冷却的前缀串号（High，`backend/agent/digest.py`）

冷却检查用 `content LIKE '[自动简评] 任务#{task_id}%'` 查历史触发消息。
`任务#1%` 会匹配到 `任务#12《…》`：

```
task #1 believes it is in cooldown because of task #12: True
```

任务 #1 只要在冷却窗口内有 #10~#19 或 #100+ 触发过，自己的简评就被静默跳过。

修复：前缀带上书名号分隔符（`任务#1《`）把编号界定住，并转义 LIKE 通配符
（任务名可能含 `%` 或 `_`）。回归用例已验证在旧代码上失败、新代码上通过。

### 1.7 单轮卡死拖垮整个 agent（High，`backend/agent/chat/runtime.py`）

chat worker 是单线程串行 drain `chat_turns`，而 `run_turn` 里
`asyncio.run(_drive(...))` 没有整轮超时。唯一的取消检查点在流式数据块之间——
模型挂起、不再发块时永远检查不到。worker 线程会永久阻塞，此后所有轮次
（包括自动简评）无限排队，且没有任何告警。

修复：`_drive_with_timeout` 用 `asyncio.wait_for` 加 15 分钟墙钟上限
（足够容纳 max_tool_calls 次往返 + 截图 + 视觉分析），超时按失败终态收尾——
已流出的文本照常落库，事件与错误消息照常写，轮次绝不悬在 `running`。

### 1.8 故障证据被调试噪声冲掉（Medium，`backend/app_logger.py`）

单一 200 条环形缓冲区。一次 `run_screener` 就写 4~5 条 info/debug，几个任务跑
一轮上百条——错误信息往往几分钟内被挤没，而排障恰恰发生在事后。这一条本身不
致命，但它是上面每一个静默失败的**放大器**：即使系统喊了，喊声也留不住。

修复：流水缓冲区扩到 1000，另设 500 条的 warn/error 专用缓冲区，查询时按单调
序号合并去重。调试噪声该被冲掉，故障证据不该。

### 1.9 下单请求被传输层盲重发（High，`backend/sources/http_client.py`）

**这一条我第一遍读代码时判错了，是对抗性验证代理把我纠正过来的，值得完整记下来。**

`fetch_with_fallback` 在 `ProxyError/ConnectionError` 且启用代理时，会用直连
session 把**同一个 URL 原样重发**。我最初认定它安全，理由是每笔订单都带
`newClientOrderId`，重发会被交易所按重复 id 拒掉——`binance_client.py` 的注释也
正是这么写的：「a transport-level resend can never double-fill」。

注释是错的，而我信了注释没去查交易所语义。真实规则是：**币安的
`newClientOrderId` 唯一性只在「订单仍然挂着（open）」时强制**。MARKET 单毫秒级
成交，成交之后同一个 id 会被当成**全新订单接受**。于是：

1. 代理把请求转发给币安，订单成交
2. 代理在响应回传前被 RST 掉 → `requests.ConnectionError`（"请求可能已送达"的歧义失败）
3. 回退逻辑盲目重发 → 第二次成交，仓位翻倍
4. 更糟的是回退**吞掉了原异常**并返回第二笔订单的成功响应，于是 service 层
   精心写的 `_query_order_state` 查单消歧**永远不会运行**，第一笔成交完全不可见

修复：`fetch_with_fallback` 增加 `allow_retry` 参数，`binance_client._request`
只对 GET 传 True。写操作让 `ConnectionError` 原样上抛，走既有的查单消歧路径——
那条路径本来就是对的，只是被传输层抢先绕过了。同时把 `binance_client.py` 里
那句误导性注释改写成准确的语义说明（幂等键的作用是**事后消歧**，不是**阻止重发**）。

教训：**代码注释不是证据**。一条断言"绝不可能发生"的注释，恰恰是最该去上游文档
核实的地方。

### 1.10 其他确认并修复的缺陷

| 缺陷 | 位置 | 说明 |
|---|---|---|
| 删除任务必然 500 | `api/tasks.py` | 清理漏了 `outcome_checks`（有 `signal_id` 外键且无级联），`foreign_keys=ON` 下凡是产生过信号的任务都永久删不掉。已改为遍历全部 5 张引用表，并加了一条**结构性护栏用例**：将来新增引用 `signals(id)` 的表时会直接测试失败 |
| 推送失败记 success | `tasks/executor.py` | `_send_push` 吞异常、`_log_push` 按默认成功落库。合并为 `_push_and_log`，按真实结果记录 |
| 已存 LLM Key 可外发 | `api/agent.py` | `{"channel_id": N, "base_url": "http://任意地址"}` 会让服务端带着解密后的密钥去连该 URL。现在：换地址测必须同时提供自己的 Key |
| 登录无爆破防护 | `auth.py` | 单一共享口令 + 无限尝试 + 失败无日志。加按来源的指数退避锁定（5 次后 30s 起、上限 15 分钟），失败写审计日志 |
| SSE 阻塞事件循环 | `api/chat.py` | `async` 路由里每 150ms 直接调同步 sqlite。平时不到 1ms 看似无害，但 `get_db` 的 busy timeout 是 10 秒——撞上写锁会把整个进程冻住十秒。改走 `run_in_threadpool` |
| 平仓单无幂等键 | `trading/service.py` | `close_position` 是全模块唯一没有 `clientOrderId`、也不捕获网络异常的下单路径：平仓到底成没成，事后无从判断。已与其余路径对齐 |
| 开发数据库进镜像 | `.dockerignore`（新增） | Docker 的 `COPY` 不看 `.gitignore`，`COPY backend/ ./` 把开发机的 `backend/data/wohub.db`（含加密的交易密钥、LLM Key、完整聊天记录）烤进镜像层。运行时被 volume 遮住所以完全看不出来，但拿到镜像的人随时能扒出来。实测旧镜像里有 16 个 `.db` 文件 |

## 2. 审计确认为「做得好」的部分

审计同样要求给出反面结论，以下几处是明确高于平均水准的工程，不要在后续重构中
无意破坏：

- **`trading/service.py` 的 bracket 下单**：每一单都带 `clientOrderId` 幂等键；
  网络异常后用 `get_order` 查证而不是盲目重发；`_order_effective()` 明确区分
  "订单存在"与"订单生效"（CANCELED 的行存在但什么也不保护）；止损放不上去就
  撤销入场（以损定仓），撤不干净时置 `naked_position` 并打 error 日志。
  这是全项目质量最高的代码。
- **主网门禁**（`_resolve` + `add_credential`）：不安全默认值下既不能新建也不能
  使用主网凭据。正因为有它，1.2 的会话伪造漏洞才没有直接演变成资金损失。
- **K线模块**：`_closed(candles)` 贯穿 indicators / structure / patterns，
  枢轴要求右侧 k 根都已收盘，量比刻意剔除当前棒避免自包含。未收盘K线污染是这类
  系统最常见的错误来源，这里处理得很干净。
- **`screenshots` 与 `chat` 的文件路由**：文件名正则白名单 + realpath +
  commonpath 二次校验，是 1.1 应该长成的样子。
- **chat 轮次的终态保证**：`_finalize_abnormal` 每一步独立兜底、`finish_turn`
  永远最后尝试，worker 循环还有一层兜底的兜底。

## 3. 已知遗留（本轮明确不做，及原因）

- **两个容器都以 root 运行**。切换非 root 用户会让容器写不了 bind-mount 的
  `./data`（宿主目录属主通常是 root），升级即故障。这需要运营者在宿主侧
  `chown` 配合，属于部署决策而非代码改动，故留给运营者：
  `chown -R 1000:1000 ./data` 后在 Dockerfile 加 `USER 1000`。
  1.1 修好之后，root 的边际风险已显著下降。
- **ChartShot 服务无鉴权**（含 `GET/PUT /api/cookies`，可读写 TradingView 登录态）。
  当前 `docker-compose.yml` 没有给它做端口映射，只在内部网络可达，风险可控。
  若将来对外暴露，必须先加鉴权。
- **`Settings.vue` 1827 行**，装了十来个互不相关的功能面板（系统信息、两套
  Cookie、代理、交易凭据、手动截图、agent 配置、LLM 渠道、语义档案、记忆、评测）。
  没有状态管理库，纯 `ref` 堆叠。建议按面板拆成子组件，但那是一次纯重构，
  与本轮的正确性/安全性主题无关，不宜混在一起做。
- **信号不去重**：条件持续时同一标的会在每根K线重复入库，`outcome_checks` 也
  随之重复排期。1.4 的时间簇聚合已经消除了它对统计结论的污染，但存储与轮询开销
  仍在。真要治理需要定义"同一信号"的语义（同标的同筛选器同周期，多久算一次？），
  是产品决策。

审计还提出了以下问题，本轮未动，按建议优先级列出以便后续处理：

| 问题 | 位置 | 为什么这轮没做 |
|---|---|---|
| 顶/底背离两个虚拟筛选器各自独立重跑基础扫描，并在不同时刻用活棒定向 | `sources/pine_screener.py` + `divergence_classify` | 同一标的可能被同时记成顶背离**和**底背离（伪"共振"），也可能双双漏掉；且基础扫描白跑两遍、限流成本翻倍。修法要重构虚拟筛选器的委派方式（跑一次基础扫描、一次定向、分发到两个 label），改动面比本轮其余修复大一个量级，且需要真实行情验证分类效果，不适合和安全修复混在一批 |
| `closePosition` 触发单在仓位关闭后无人善后 | `trading/service.py` | TP 成交或手动平仓后，残留的另一条触发单会误伤下一笔仓位。正确解法是 OCO 语义或持仓事件驱动的清理，属于新增能力而非修 bug |
| `/tasks/{id}/test` 在请求线程里跑完整流水线 | `api/tasks.py` | 绕开调度器的 `max_instances`，可与定时执行并发重复写信号。修法是把手动测试也纳入调度器，属于结构调整 |
| 交易所行情解析对空串/null 零容错 | `sources/bybit.py` 等 | 单个坏字段会丢掉整个交易所的数据。需要逐个交易所对真实响应做容错，本轮无法验证 |
| 交易表单不校验 SL/TP 与方向、换标的后残留上一个标的的计划值 | `frontend/.../TradeForm.vue` | 真实的资金安全问题（后端会拒单或回滚，但不该依赖这层）。修法涉及表单状态机重整，建议与 `Settings.vue` 拆分一起做前端专项 |
| 轮换 `SECRET_KEY` 后 agent 渠道面板整体 500 | `agent/config.py` | 解密失败没有降级路径，只能手工改 SQLite 才能恢复。「密钥拿得出来又换不动」，应补一条可恢复的重录流程 |
| 停用的渠道仍会被推送 | `channels/` | 需要确认是产品意图还是缺陷 |

## 4. 验证

- 全量测试 543 → 595（+52），全绿，无回归；`npm run build` 通过
- 关键修复均先在**旧代码上复现故障**再实现：路径穿越用真实 uvicorn 服务端
  验证前后行为；调度器丢任务用项目原配置复现 3 选 1；简评串号用
  `git stash` 回退后确认新用例确实失败；假阳性率用蒙特卡洛量化
- 新增用例刻意覆盖"故障时的表象"而不只是happy path，因为本轮 8 个缺陷里 7 个
  的问题恰恰是表象正常
- **真实服务端端到端验收**（不是测试客户端，因为百分号解码和 cookie 校验都发生在
  真实 ASGI 栈里）：

  | 检查 | 结果 |
  |---|---|
  | `GET /api/health` | 200，数据库已连接 |
  | 无 cookie 访问受保护接口 | 401 |
  | **用公开默认 SECRET_KEY 伪造的 cookie** | **401**（修复前：完全放行） |
  | 正常登录后访问 | 200 |
  | `GET /%2e%2e%2fdata%2fwohub.db` | 回落 SPA 外壳，不泄漏（修复前：吐出整个库） |
  | `GET /api/<不存在>` | 404（修复前：200 + HTML） |
  | `data/session_key` | 已生成，权限 0600 |
  | 启动告警 | 同时出现在 stderr 与 `/api/settings/logs` |

- **生产镜像内的前后对照**。§1.1 的路由只在生产镜像里存在，所以最后用
  `docker build` 出修复前（HEAD）与修复后两个镜像，各自起容器实打实地打：

  | 攻击 | 修复前镜像 | 修复后镜像 |
  |---|---|---|
  | `GET /%2e%2e%2f…%2fetc/passwd` | `root:x:0:0:root:/root:/bin/bash` | SPA 外壳 |
  | `GET /%2e%2e%2fconfig.py` | 吐出源码（含 `DEFAULT_APP_PASSWORD = "admin"`） | SPA 外壳 |
  | `GET /%2e%2e%2fdata%2fwohub.db` | 整个数据库 | SPA 外壳 |
  | 默认密钥伪造的 cookie | **HTTP 200**，受保护接口全开 | HTTP 401 |
  | 连续 6 次错误口令 | `401 401 401 401 401 401` | `401 401 401 401 401 429` |
  | 未匹配的 `/api/*` | 200 + HTML | 404 |
  | 镜像内 `.db` 文件 | 16 个（含开发库与 pytest 临时库） | 0 个 |

  过程中有一次差点被假信号骗过：容器因端口被占用而根本没启动，`curl` 打到的是
  一个残留的测试进程，返回的 SPA 外壳看起来**恰好像是修复生效**。核对
  `docker inspect` 的状态才发现容器状态是 `created`。教训是通过测试的输出同样
  要验证来源——尤其当结果正好符合预期时。

## 5. 升级注意事项

1. **默认配置的部署升级后需要重新登录一次**（会话密钥变更）。已存的交易 API
   secret 与 LLM API key **不受影响**，无需重新录入。
2. `data/` 目录会新增 `session_key` 文件（0600）。**它和数据库一样需要备份**，
   删掉会导致所有人重新登录。
3. 若从未在「系统设置 → Pine Cookie」配置过 TradingView 登录态，升级后日志会
   出现明确告警。这不是升级引入的故障——是原先被硬编码令牌掩盖的既有问题浮出
   水面，请补配 Cookie。
4. 调度器行为变化：同时到期的任务现在**排队**而非被丢弃。如果此前配了多个任务，
   升级后会看到任务执行数量显著上升（本来就该如此），随之而来的是 TradingView
   请求量上升，注意限流。
5. 验证器结论会变严：此前判 `pass` 的语义档案可能变成 `fail` 或
   `not_validated`。这不是退化，是此前的结论本就不成立。
6. 连续 5 次密码错误会触发按来源的锁定（30 秒起，指数退避，上限 15 分钟）。
   反向代理后面请确保透传 `X-Forwarded-For`，否则所有用户会被算作同一个来源而
   相互牵连。该头可伪造，因此**只用作限流维度，绝不用于鉴权**。
7. `.dockerignore` 是新增文件，**重新构建镜像才会生效**。已有的镜像层里仍然带着
   构建当时的开发数据库，按泄漏处理：重建并替换，不要只滚动容器。

## 6. 方法论备注

本轮值得保留的三条做法：

1. **先复现，再修**。每一条修复都先在旧代码上让故障重现（真实 uvicorn 服务端、
   项目原调度器配置、`git stash` 回退后跑新用例、蒙特卡洛量化假阳性率），
   再实现。这样得到的用例是真的回归用例——旧代码上失败、新代码上通过——
   而不是「顺手补的测试」。
2. **在真正会部署的产物上验收**。§1.1 的漏洞只存在于生产镜像，任何本地测试都
   碰不到它。最终验收因此是 `docker build` 两个镜像实打实地打。
3. **让别人试图推翻你的结论**。§1.9 是我读代码时判错、被独立验证代理纠正的
   一条真实资金风险——我信了代码注释而没去查交易所语义。同一批验证代理也
   正确地"推翻"了几条我已经修好的发现（它们读的是修复后的工作区），这本身
   是对修复的独立确认。

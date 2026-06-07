# 安全默认门 + 测试网端到端验证 设计

日期：2026-06-07
状态：已与用户确认设计，待写实现计划

## 背景与目标

自动化交易就绪度扫描发现两件事，本设计聚焦其中的「先验证 + 补安全默认」一路（用户选定）：

1. **不安全默认值是 blocker**：`config.py` 中 `APP_PASSWORD` 默认 `admin`、`SECRET_KEY` 默认 `change-me-in-production`，启动**不校验**。`SECRET_KEY` 既签发登录 session，又派生加密 Binance API secret 的 Fernet 密钥——默认值意味着拿到 DB 文件即可解密所有 key，能访问到服务即可以 `admin` 登入。唯一把这变成「真金白银损失」的是一次**主网**下单。
2. **测试网端到端链路尚未实测**：`build_position_plan → place_order_bracket` 已实现但从未对真实 Binance 跑过；这是自动化层将复用的核心单元。

**目标**：
- (G1) 在不安全默认配置下，**禁止一切主网活动**并在启动时大声警告；测试网、本地开发、启动、`pytest` 完全不受影响。
- (G2) 提供一个**服务层一键脚本**，让用户带测试网凭据验证 `/plan → /order/bracket → open-orders → position/close` 全链路，含两个对抗场景（精度/最小名义额拒单、入场成 SL 拒的「裸仓」复现），自动收尾。

**非目标**（属后续 Option B/自动化层，不在本次）：括号单 SL 失败自动恢复、`clientOrderId` 幂等、单订单状态查询、账户级风控护栏、自动化任务类型。本设计仅**验证**现状并**关闭主网暴露面**。

## 决策（已确认）

1. **网关行为**：只拦主网 + 启动大声警告（非「硬拒启动」，非「只警告不拦」）。
2. **拦截机制**：抛 `ValueError`，复用现有 API 层错误映射（不引入自定义异常 + 全局 handler——因多数端点有 `except Exception` 会先吞掉自定义异常，改全局 handler 反而要逐个端点改）。
3. **验证工具形态**：服务层一键脚本 + 临时 DB（非 HTTP 驱动，非 pytest 用例）。

## 交付物 1：安全默认门

### 1.1 `backend/config.py` — 默认常量 + 判定方法

把两个不安全默认值提为模块常量（消除「判定字符串」与「默认值」漂移），新增判定方法：

```python
DEFAULT_APP_PASSWORD = "admin"
DEFAULT_SECRET_KEY = "change-me-in-production"

class Settings:
    def __init__(self):
        self.app_password = os.environ.get("APP_PASSWORD", DEFAULT_APP_PASSWORD)
        self.secret_key = os.environ.get("SECRET_KEY", DEFAULT_SECRET_KEY)
        # ... 其余不变 ...

    def insecure_defaults(self) -> list[str]:
        """仍处于不安全默认值的安全关键设置名（用于主网门 + 启动警告）。"""
        bad = []
        if self.secret_key == DEFAULT_SECRET_KEY:
            bad.append("SECRET_KEY")
        if self.app_password == DEFAULT_APP_PASSWORD:
            bad.append("APP_PASSWORD")
        return bad
```

两者都拦主网，理由不同：`SECRET_KEY` 默认 → 存储 secret 可解密 + session 可伪造；`APP_PASSWORD` 默认 → 任何人可登入。

### 1.2 主网拦截点（两道，均抛 `ValueError`）

**点 A — `backend/trading/service.py` 的 `_resolve()`**：每个触达 Binance 的服务函数（`test_credential`、`get_account`、`place_order`、`place_order_bracket`、`close_position`、`list_open_orders`、`cancel_open_order`、`list_binance_order_history`、`build_position_plan`）都经此解析凭据，是单一收口处。

```python
def _resolve(credential_id: int) -> tuple[str, str, str]:
    creds = get_credential(credential_id)
    if not creds:
        raise ValueError(f"credential {credential_id} not found or disabled")
    env = creds[0]
    if env == "mainnet":
        bad = settings.insecure_defaults()
        if bad:
            raise ValueError(
                "拒绝在不安全的默认配置下使用主网凭据：" + "、".join(bad) +
                " 仍为默认值。请设置强随机的 SECRET_KEY 与 APP_PASSWORD 后重启。"
                "（注意：设置或轮换 SECRET_KEY 会使已加密的 API secret 失效，需重新录入。）"
            )
    return creds
```

**点 B — `backend/trading/credentials.py` 的 `add_credential()`**：默认配置下禁止**新建**主网凭据（在既有 `env` 校验之后追加判定）：

```python
def add_credential(label, env, api_key, api_secret):
    if env not in ("testnet", "mainnet"):
        raise ValueError(...)
    if env == "mainnet" and settings.insecure_defaults():
        raise ValueError(
            "拒绝在不安全的默认配置下新建主网凭据：" +
            "、".join(settings.insecure_defaults()) +
            " 仍为默认值。请设置强随机的 SECRET_KEY 与 APP_PASSWORD 后重启。"
        )
    if not api_key or not api_secret:
        raise ValueError(...)
    ...
```

**状态码**：沿用现有映射。`/order`、`/order/bracket`、`/plan`、`/credentials`（add）端点 `except ValueError → 400`（资金路径干净）；`/account`、`/position/close`、`/open-orders`、`/history` 端点 `except ValueError → 404`（读路径状态码不完美，但 detail 文案明确，刻意不为此铺开改每个端点）。

### 1.3 启动警告 — `backend/main.py` lifespan

`init_db(...)` 之后、`start_all_enabled()` 之前：

```python
bad = settings.insecure_defaults()
if bad:
    msg = ("不安全的默认配置：" + ", ".join(bad) + " 仍为默认值；"
           "主网交易已被禁用。设置强随机值后重启。")
    applog("security", "warn", msg)
    print("\n" + "=" * 60 + f"\n⚠️  WoHub 安全警告：{msg}\n" + "=" * 60 + "\n",
          file=sys.stderr, flush=True)
```

经 `app_logger`（进系统日志环形缓冲，前端可见）**且**写 `stderr`（确保 `docker compose logs` 可见）。需在 `main.py` 顶部 `import sys`。

### 1.4 为何不拦测试网

测试网 key 低价值；拦了会把本设计要启用的 G2 验证也堵死。本地开发便利保留。门在调用时读 `settings`，`pytest` 可 monkeypatch，故不影响测试。

## 交付物 2：`backend/scripts/verify_testnet.py`

### 2.1 接口与隔离

运行（PowerShell）：
```powershell
$env:BINANCE_TESTNET_KEY="..."; $env:BINANCE_TESTNET_SECRET="..."
python scripts/verify_testnet.py --symbol BTCUSDT --interval 15m
```
（bash 同义。）

- key/secret 走环境变量 `BINANCE_TESTNET_KEY` / `BINANCE_TESTNET_SECRET`；缺失则交互提示，secret 用 `getpass`——**永不打印 secret**。
- 参数：`--symbol`(默认 BTCUSDT)、`--interval`(默认 15m)、`--risk-pct`(默认 1.0)、`--rr`(默认 1.5)、`--leverage`(默认 5)、`--yes`(跳过二次确认)、`--keep`(跳过收尾、打印手动清理命令)。
- **隔离**：`main()` 最开头（任何 `config`/`service` 导入之前）`os.environ["DB_PATH"] = <临时目录>/verify.db`，再 `init_db`，跑完删临时目录。绝不污染真库。模块顶层只导入标准库；`config`/`trading.*` 在设置完 env 后于 `main()` 内导入。
- 全程走 `trading.service`（= 自动化将复用的那层）。**无需起服务器、无需登录、无需改任何现有服务/客户端代码**（所用函数均已存在）。

### 2.2 执行序列（每步打 PASS/FAIL/SKIP，`try/finally` 保证收尾）

| 步 | 动作 | 验证点 |
|---|---|---|
| 0 | 确认 env=testnet + 二次确认（`--yes` 跳过） | 脚本仅创建 testnet 凭据；二次保证 `creds[0]=='testnet'` |
| 1 | `add_credential`(testnet) → `test_credential(id)` | 账户可达；-2015（IP/权限）等错误原样透出后 FAIL |
| 2 | `get_account(id)` | 打印净值/可用/持仓；净值≤0 时 WARN（无法定仓） |
| 3 | `build_position_plan(... long, MARKET ...)` | 打印结构枢轴/entry/SL/TP/qty/feasible/warnings；断言 `feasible=True`（否则解释原因）；**用真实 exchangeInfo 过滤器 + 真实净值** |
| 4 | **正常括号单** `place_order_bracket(entry+SL+TP)` | 断言 entry/SL/TP 全 ok；`list_open_orders` 见 2 张 `closePosition=true`（STOP_MARKET + TAKE_PROFIT_MARKET）→ 证明 SL/TP 真挂上；随后 `cancel_open_order`×2 + `close_position` 平掉，`get_account` 确认空仓（顺带验证平仓） |
| 5 | **故意拒单** 提交 `notional < min_notional` 的单 `place_order` | 断言 `result.ok=False` 且 error 含 Binance 过滤器码（-1013/-4164/-1111 等）→ 证明拒单被干净上报而非崩溃 |
| 6 | **裸仓复现** long 入场 + `stop_loss_price = 现价×1.05`（错侧，-2021 立即触发拒绝），仅传 SL 不传 TP | 断言 entry.ok=True、sl.ok=False、overall=False；`get_account` 见持仓且 `list_open_orders` 无止损 → **当场复现「裸仓」缺陷**，打红色 WARN；**立即 `close_position` 平掉**（既验真实行为，又用事实证明 Option B 括号恢复为何必须） |
| 7 | 收尾 | 撤净所有单 + 平掉持仓 + 删临时 DB；打印汇总表；全关键步通过 → exit 0，否则 exit 1 |

### 2.3 安全特性

- **仅 testnet**：解析到 `env!='testnet'` 直接拒跑。
- **确定性触发**：步 5 用子最小名义额（由 `pp.parse_filters` + entry 价算出）；步 6 用错侧止损（long 的 SL 设在现价上方 5%，Binance 必返 -2021）。
- **收尾在 `finally`**：即使中途异常也撤单 + 平仓。`--keep` 保留现场并打印手动清理命令。
- 开跑前 best-effort 清掉该 symbol 上次残留单/仓。
- 可提交进仓库当测试网回归脚本。

### 2.4 纯逻辑抽取（便于离线 sanity）

把无副作用的小计算抽成模块级纯函数：
- `sub_min_notional_qty(entry_price, filters) -> float`：返回一个 `notional < min_notional`、且按 step_size 取整后 > 0 的 qty（步 5 用）。
- `wrong_side_stop_price(direction, entry_price) -> float`：long 返回 `entry×1.05`、short 返回 `entry×0.95`（步 6 用）。

便于 `pytest -m "not network"` 离线验证而不触网/不下单。

## 测试

### 门（单测，离线）
- `config.insecure_defaults()`：双默认 → `["SECRET_KEY","APP_PASSWORD"]`；两者均覆盖 → `[]`；各覆盖一个 → 对应单元素列表。（monkeypatch `settings.secret_key`/`settings.app_password`）
- `service._resolve()`：mainnet 凭据 + 默认 → 抛 ValueError；testnet 凭据 + 默认 → 不抛；mainnet 凭据 + 非默认 → 不抛。（monkeypatch `settings` + monkeypatch `get_credential` 返回相应 env 三元组）
- `credentials.add_credential()`：`env='mainnet'` + 默认 → 抛 ValueError；`env='testnet'` + 默认 → 放行。（monkeypatch `settings` + 临时库 DB_PATH）

### 门（API 级）
- monkeypatch `settings` 为默认值后 `POST /api/trading/credentials` `env='mainnet'` → 400；`env='testnet'` → 非 400。（沿用 autouse 鉴权 override；conftest 默认 `SECRET_KEY=test-secret-key`、`APP_PASSWORD=testpass` 均非默认，故须在测试内 monkeypatch 成默认值）

### 脚本
- 验证工具本身不写端到端单测（它就是验证工具，且触网下单）。
- 仅对抽出的纯函数 `sub_min_notional_qty`、`wrong_side_stop_price` 写离线单测。
- `pytest -m "not network"` 全绿不被影响。

## 范围与非目标

- 不改鉴权模型（仍单 `APP_PASSWORD` + 签名 cookie）。
- 不改前端（本次纯后端 + 脚本）。
- 不实现括号恢复/幂等/状态查询/风控护栏/自动化任务（后续）。
- `_resolve` 抛 ValueError 导致 `account/close` 等读路径状态码为 404（已知取舍，文案明确）。

## 安全说明

此交付物使「主网下单必须在安全配置之后」成为服务端硬约束，关闭默认密钥下的真金白银暴露面；并通过测试网脚本在零资金风险下验证已建的计划→括号→平仓链路、且当场复现尚未修复的裸仓缺陷，为后续 Option B 提供事实依据。相关上游分析见自动化就绪度扫描（本会话）。

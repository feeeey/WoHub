# Bracket SL Recovery — 裸仓自动恢复设计

日期：2026-06-10
状态：已批准（方向在 2026-06-07 会话确认为 Option B 首项；本设计在 /goal 自主模式下依据
已记录的用户交易方法「以损定仓」裁定细节）

## 问题

`place_order_bracket` 目前是 best-effort：入场单成交后，若止损单（STOP_MARKET,
closePosition=true）被拒绝，函数只返回错误，**留下一个没有止损的实盘持仓**。
仓位大小由止损距离反推（以损定仓），没有止损的持仓风险无界，直接违背交易方法。
主网启用前必须修复。

两个底层事实加剧了风险：

1. `http_client.fetch_with_fallback` 在代理失败时会用直连**重发同一个 POST**。
   代理路径可能已把订单送达 Binance —— 同一笔下单可能成交两次。
2. `requests.Session` 没有 `timeout` 属性，`s.timeout = 10` 是无效代码——当前所有
   外呼**没有超时**，挂起连接会无限阻塞（交易请求在交互式 API 里尤其不可接受）。

## 方案对比

| 方案 | 描述 | 取舍 |
|------|------|------|
| A. 仅告警 | 标记 naked_position，前端红色横幅，人工处理 | 实现最小；但违背以损定仓——用户必须恰好在线盯盘 |
| **B. 校验后撤销（选定）** | 重试分类 + clientOrderId 幂等校验；止损确定无法挂上时自动撤销入场（撤未成交单 + 市价平已成交量） | 确定性安全：永远不存在"挂不上止损的持仓"；-2021（触发价已越过）时市价平仓 ≈ 止损本来要做的事 |
| C. 合成止损看门狗 | 保留持仓，服务端轮询价格、到价市价平仓 | 自己重新实现交易所的本职工作；进程重启/轮询间隙即裸奔；属于后续 reconciliation 循环的课题 |

选 B。原则一句话：**止损挂不上，这笔交易就不该存在。**

## 设计

### 1. binance_client 增量

- `place_order(..., new_client_order_id: str | None = None)` → 透传 `newClientOrderId`。
- 新增 `get_order(env, api_key, api_secret, symbol, order_id=None, orig_client_order_id=None)`
  → `GET /fapi/v1/order`（signed）。订单不存在时 Binance 返回 -2013，由调用方解释为"未挂上"。
- `cancel_order` 扩展为接受 `order_id` 或 `orig_client_order_id` 之一。
- 错误分类常量：
  - `RETRYABLE_CODES = {-1001, -1003, -1007, -1021}`（内部错误/限频/超时/时间窗）
  - HTTP 429、418、>=500 同样视为可重试。
  - 其余 BinanceAPIError 一律 fatal（默认 fatal 更安全：-2021 立即触发、-4xxx 过滤器、
    -1111 精度等重试无意义）。
- 传输层异常（`requests.RequestException`）继续向上抛——含义是**状态未知**（歧义），
  不是失败。

### 2. http_client 修复

`fetch_with_fallback` 内 `kwargs.setdefault("timeout", 10)`；删除两处无效的
`s.timeout = 10`。全局生效（行情抓取同样受益），最坏情况由"无限挂起"变为 10s 失败。

### 3. models 增量

```python
@dataclass
class RecoveryResult:
    attempted: bool                       # 是否进入撤销流程
    entry_cancel: OrderResult | None      # 撤销未成交入场单的结果
    close: OrderResult | None             # 市价平已成交量的结果
    naked_position: bool                  # 撤销后仍残留无保护持仓
    detail: str                           # 中文说明，前端直接展示
```

`BracketOrderResult` 增加 `recovery: RecoveryResult | None = None`，`to_dict` 同步。

### 4. service 流程

所有我方下出的订单（入场 / SL / TP / 恢复平仓）一律生成
`clientOrderId = f"wohub-{secrets.token_hex(10)}"`：

- **传输层重发免疫**：同一 clientOrderId 重复提交会被 Binance 拒绝，fetch_with_fallback
  的代理→直连重试不再可能造成双重成交。
- **歧义可解**：网络异常后用 `get_order(orig_client_order_id=...)` 查证真实状态。

**入场单**（place_order，不自动重试——重试入场有双仓风险，失败让用户重新提交）：

- `BinanceAPIError` → 失败（现状不变）。
- `RequestException` → `get_order` 查证：存在 → 按成功继续（raw 取查询结果）；
  -2013 → 干净失败；查证本身也失败 → 失败并在 error 中标注"状态未知，请检查持仓"，
  applog error。

**保护单**（`_place_protection_with_retry`，SL 与 TP 共用）：

- 最多 3 次尝试，退避 (0.5s, 1.0s)，**全程复用同一个 clientOrderId**。
- fatal BinanceAPIError → 立即返回失败。
- retryable → 退避后重试。
- RequestException → get_order 查证：存在 → 成功；不存在 → 计一次失败后重试；
  查证失败 → 再试一次查证，仍失败则按失败返回（撤销流程里会对该 clientOrderId
  做兜底撤单，保证不留孤儿触发单）。

**place_order_bracket 编排**：

1. 入场失败 → 返回（不变）。
2. 请求了 SL → `_place_protection_with_retry(STOP_MARKET)`。
   失败 → **跳过 TP**，执行 `_undo_entry`，返回 `ok=False` + recovery。
3. SL 成功（或未请求）→ TP 照常（同一重试助手）。TP 失败**不撤销**（止损已在，
   持仓有保护），维持现状语义：ok=False，提示手动补 TP。

**`_undo_entry`（撤销流程）**：

1. 兜底撤销可能成为孤儿的 SL 触发单（按 clientOrderId 撤单，-2011/-2013 忽略）。
2. 入场单 status 为 NEW / PARTIALLY_FILLED → 撤单（撤单响应含最终 executedQty）。
3. `position_risk(symbol)` 查当前持仓：
   - 持仓为 0 或方向与入场相反（说明本来就有反向仓）→ 无需平仓。
   - 平仓量 = `min(入场最终成交量, |positionAmt|)`——只平本次入场带来的量，
     不动用户既有同向仓。
4. reduce-only MARKET 平仓（自带 clientOrderId，同样做歧义查证一次）。
5. 每一步都 `_record_order` 入审计表；失败用 applog error。
6. `naked_position = True` 当且仅当流程结束后仍可能残留本次入场的无保护持仓
   （平仓失败或状态未知）。`detail` 用中文完整描述发生了什么。

### 5. API 层

`/trading/order/bracket` 响应经 `to_dict()` 自动携带 recovery 字段，无路由改动。

### 6. 前端（Trade.vue）

bracket 提交回调里按优先级展示：

- `recovery?.naked_position` → 持久红色警示条：
  「⚠️ 止损设置失败且自动撤销未完成——当前可能存在无止损持仓，请立即到持仓页手动处理！」
- `recovery && !naked_position` → 警告提示：
  「止损单设置失败，已自动撤销本次入场（以损定仓：无止损不持仓）。」
- 仅 TP 失败 → 现有警告路径（持仓有止损保护，提示手动补 TP）。
- `entry.warning`（保证金模式降级）→ 普通提示。

### 7. 显式不做（YAGNI / 边界）

- 不加配置开关（`on_sl_failure` 等）：无止损不持仓是交易方法本身，不是偏好。
- 不改 DB schema：clientOrderId 已包含在 response_json 原文中，审计可查。
- 双向持仓（hedge mode）不支持，与现状一致（项目假定单向持仓）。
- closePosition 触发会平掉整个 symbol 持仓（含既有同向仓）——现状行为，不在本次范围。
- 入场单不自动重试。

### 8. 测试

纯函数：错误分类 `_is_retryable`；clientOrderId 格式。

service 级（monkeypatch `bn.*` 与 sleep）：

1. SL fatal（-2021）→ 不试 TP；入场被撤销/平仓；`ok=False`，`recovery.close.ok=True`。
2. SL 瞬态失败一次后成功 → `ok=True`，无 recovery。
3. SL 网络歧义但 get_order 查到 → 视为成功，不重复下单。
4. SL 失败且平仓也失败 → `naked_position=True` + applog error。
5. LIMIT 入场未成交、SL 失败 → 撤入场单，不需要平仓。
6. 入场网络歧义：get_order 查到已成交 → 正常继续；查无 → 干净失败。
7. TP 失败（SL 已挂）→ 不撤销，`ok=False`，持仓保留。
8. 既有同向仓时撤销只平入场量（min 逻辑）。
9. http_client：fetch_with_fallback 默认带 timeout=10，调用方可覆盖。

E2E：`scripts/verify_testnet.py` 已含 naked-position 场景，实现后由用户在测试网跑通。

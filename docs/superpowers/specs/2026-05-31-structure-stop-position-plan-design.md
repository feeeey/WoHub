# 结构化止损 + 以损定仓 开单方案（交易计划器）设计

日期：2026-05-31
状态：已与用户确认设计，待写实现计划

## 背景与目标

用户的开单习惯：**以损定仓**（固定单笔风险）、**固定盈亏比 ≥ 1.5**、止损放在**结构前低/前高再外一点**的位置。本功能为后续自动化交易打基础：给定标的、周期、方向，**快速找到结构前低/前高**，据此一次性算出可直接填入开仓表单的**止损价 / 止盈价 / 开单量**。

本期范围（用户确认）：**完整开单参数** —— 结构点 → 止损价 → 1.5 盈亏比止盈 → 以损定仓开单量。

## 核心决策（已确认）

1. **结构算法**：分形枢轴（Fractal pivot）。
2. **止损缓冲**：ATR 倍数（随波动自适应）。
3. **风险额**：账户净值百分比（需拉账户余额）。
4. **打包方式**：分层 —— `klines/structure.py`（纯 TA）+ `trading/position_plan.py`（算仓）+ `POST /trading/plan`（编排，只读）+ 交易终端「📐 智能计算」按钮。

## 架构与数据流

```
前端「📐 智能计算」按钮
   │ direction(做多/做空)、order_type、limit价、risk%、rr、atr_mult
   ▼
POST /trading/plan  （只读，绝不下单；在 cookie 鉴权之后）
   ├─ fetch_klines(symbol, interval, limit=lookback)          # 复用 klines/fetcher
   ├─ klines/structure.py
   │     ├─ find_pivot(candles, direction, ref_price, k, lookback)  # 分形枢轴
   │     └─ atr(candles, period)                              # Wilder ATR
   ├─ trading.service.get_account(credential_id)              # 复用；权益 = 钱包余额 + 未实现盈亏
   ├─ trading.binance_client.exchange_info(...) → symbol filters  # 复用
   └─ trading/position_plan.py → 组装方案
   ▼
返回 {结构点, ATR, 入场价, 止损价, 止盈价, 风险额, 权益, 开单量, 名义价值, 所需保证金, 可行性, warnings}
   ▼
前端填入 数量/止损/止盈 + 图上画结构线，人工复核 → 走【现有】bracket 下单（下单逻辑不改）
```

**模块边界**
- `klines/structure.py`：纯函数，不碰网络/凭据。可离线单测，可被筛选器/未来策略复用。
- `trading/position_plan.py`：组装与交易所精度对齐，需要账户与 exchangeInfo。

## 算法细节

### 分形枢轴 `find_pivot`（structure.py）
- 仅在**已收盘**K线上识别。
- 枢轴低点 at i：`low[i] < low[j]` 对左右各 K 根都成立（strict `<`）。需右侧 K 根均已收盘确认 → 最新枢轴至少在 K 根之前。
- **做多**：从最新往回扫，取**第一个 `low < 入场价` 的已确认枢轴低点**（止损必须在入场下方）。
- **做空**：对称取**第一个 `high > 入场价` 的已确认枢轴高点**。
- `lookback` 根内找不到 → 返回 `None`（触发 §兜底）。
- 返回：结构价、bar 在序列中的索引、bar 开盘时间、距今多少根（age_bars）。

### ATR `atr`（structure.py）
- Wilder RMA 平滑。True Range = `max(H−L, |H−prevC|, |L−prevC|)`，用已收盘K线。
- 周期默认 14。数据不足返回 `None`。

### 止损价（position_plan.py）
- 做多：`SL = 结构low − atr_mult × ATR`；做空：`SL = 结构high + atr_mult × ATR`。
- 向**安全侧**对齐 tickSize：做多向下取整、做空向上取整（保证止损不被对齐到比预期更紧）。

### 止盈价
- 做多：`TP = entry + rr × (entry − SL)`；做空：`TP = entry − rr × (SL − entry)`。
- 对齐 tickSize（朝对用户有利或就近，明确在实现中固定为就近 round）。

### 开单量（以损定仓）
- `风险额 = 权益 × (risk_pct / 100)`；`权益 = total_wallet_balance + total_unrealized_pnl`（= marginBalance）。
- **单位约定**：`risk_pct` 以百分数计，`1.0` 表示 1%（不是 100%）。`rr`、`atr_mult`、`atr_fallback_mult` 为普通倍数。
- `数量 = 风险额 / |entry − SL|`，向下对齐 stepSize。
- 校验 `数量 ≥ minQty` 且 `数量 × entry ≥ minNotional`；不满足 → `feasible=false` + warning。
- 该量与杠杆无关。

### 所需保证金与可行性
- `所需保证金 = 数量 × entry / 杠杆`。与 `available_balance` 比较，不足 → warning。

## 入场价取法
- 限价单：用用户填写的限价。
- 市价单：用实时价 = `fetch_klines` 最后一根**未收盘**K线的 `close`（不额外请求）。

## API 契约
`POST /trading/plan`（只读，不下单，cookie 鉴权后）

请求 body：
```json
{
  "credential_id": 1,
  "symbol": "BTCUSDT",
  "interval": "4h",
  "direction": "long",          // long | short
  "order_type": "MARKET",       // MARKET | LIMIT
  "entry_price": null,          // LIMIT 时必填，MARKET 时忽略
  "risk_pct": 1.0,
  "rr": 1.5,
  "atr_mult": 0.3,
  "atr_period": 14,
  "fractal_k": 2,
  "lookback": 150,
  "leverage": 10
}
```

响应：
```json
{
  "structure_found": true,
  "structure": { "price": 0.0, "bar_index": 0, "bar_time": 0, "age_bars": 0 },
  "atr": 0.0,
  "entry_price": 0.0,
  "stop_price": 0.0,
  "stop_distance": 0.0,
  "take_profit_price": 0.0,
  "rr": 1.5,
  "risk_pct": 1.0,
  "risk_amount": 0.0,
  "equity": 0.0,
  "quantity": 0.0,
  "notional": 0.0,
  "required_margin": 0.0,
  "feasible": true,
  "warnings": []
}
```

## 前端
交易终端 `TradeForm.vue` 增加「📐 智能计算」按钮 + 三个小输入（risk%、盈亏比 rr、ATR 倍数，带默认值）。
- direction 由现有 做多/做空（BUY/SELL）推导；order_type、limit 价取自表单。
- 点击 → 调 `/trading/plan` → 自动填 `quantity / stop_loss_price / take_profit_price` 并勾上 SL/TP。
- 图上画 结构点（实线）、SL/TP（虚线），复用 `Trade.vue` 的 `redrawPriceLines`。
- 显示小结：风险额、结构点距今 N 根、所需保证金、warnings。
- 用户复核后点开仓，走**现有** bracket 流程（不改下单逻辑）。

## 边界处理
- **找不到结构**：`structure_found=false`，兜底 `SL = entry ∓ atr_fallback_mult × ATR`，warning 标注“未找到结构，已用 ATR 兜底”，前端醒目提示。
- **minNotional / minQty 不满足** 或 **保证金不足**：`feasible=false` + warning；前端禁用填入或红字提示，绝不静默放过。
- **ATR 不可得 / K线不足**：返回明确错误。
- **网络/数据失败**：沿用现有 `KlineRequestError` / `BinanceAPIError` 处理。
- 全程不记录、不返回 API secret。

## 测试
- `structure.py`（纯函数，离线）：多/空枢轴识别、strict 比较、右侧未确认不计、找不到返回 None、ATR 数值向量、age_bars 计算。
- `position_plan.py`：SL/TP/数量 公式、tickSize/stepSize 对齐方向、minNotional 与 minQty 边界、保证金可行性、ATR 兜底、做空对称。
- 走现有 pytest，`-m "not network"` 可全离线运行。

## 默认参数（已确认）

| 参数 | 默认 | 说明 |
|---|---|---|
| `fractal_k` | 2 | 左右各 2 根 → 5 根分形 |
| `atr_period` | 14 | ATR 周期 |
| `atr_mult` | 0.3 | 止损缓冲 = 0.3 × ATR |
| `rr` | 1.5 | 盈亏比，默认 1.5 可调高 |
| `risk_pct` | 1% | 单笔风险 = 权益 × 1% |
| `lookback` | 150 | 往回扫多少根找枢轴 |
| `atr_fallback_mult` | 1.5 | 找不到结构时兜底止损距离 |

权益口径：`marginBalance = total_wallet_balance + total_unrealized_pnl`（复用现有 `get_account()`）。

## 安全约束（沿用既有）
- API key 仅“合约交易”权限、无提现；币安侧绑定 VPS IP。
- 所有交易端点在 cookie session 鉴权之后；`/trading/plan` 为只读。
- API secret 绝不出现在任何日志/响应/报错中。
- VPS 不需要本地 10809 代理（代理不通自动回退直连）。

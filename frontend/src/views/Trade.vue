<template>
  <div>
    <div class="page-header">
      <h1>交易终端</h1>
      <p>K线 + 形态识别 + 币安永续下单 · 一处搞定</p>
    </div>

    <!-- Empty state -->
    <div v-if="!credentials.length" class="card empty-card">
      <h3>尚未配置 API Key</h3>
      <p>请先到「系统设置 → 交易凭据」添加 Binance API key。建议先用测试网（testnet）验证后再切实盘。</p>
      <router-link class="btn btn-primary" to="/settings">前往设置</router-link>
    </div>

    <template v-else>
      <!-- Toolbar -->
      <div class="kline-toolbar card">
        <div class="ctrl">
          <label>币种</label>
          <input v-model.trim="symbol" class="symbol-input" placeholder="BTCUSDT" @keyup.enter="loadAll" />
        </div>
        <div class="ctrl">
          <label>周期</label>
          <select v-model="interval" class="interval-select">
            <option v-for="iv in intervals" :key="iv" :value="iv">{{ iv }}</option>
          </select>
        </div>
        <div class="ctrl">
          <label>根数</label>
          <input v-model.number="limit" type="number" min="20" max="500" class="limit-input" />
        </div>
        <div class="ctrl checkbox">
          <label>
            <input v-model="includeCurrent" type="checkbox" />
            <span>识别当前未收盘K线</span>
          </label>
        </div>
        <div class="ctrl level-ctrl">
          <label>分类层级</label>
          <div class="level-chips">
            <button v-for="lv in levelOptions" :key="lv.key"
                    class="level-chip" :class="{ active: enabledLevels[lv.key] }"
                    :title="lv.title" @click="toggleLevel(lv.key)">{{ lv.label }}</button>
          </div>
        </div>
        <div class="ctrl">
          <label>自动刷新</label>
          <select v-model.number="refreshSec" class="refresh-select">
            <option :value="0">关闭</option>
            <option :value="10">10s</option>
            <option :value="30">30s</option>
            <option :value="60">60s</option>
            <option :value="300">5min</option>
          </select>
        </div>
        <div class="ctrl-spacer"></div>
        <div class="ctrl">
          <span class="refresh-dot" :class="{ loading }"></span>
          <span v-if="refreshSec > 0" class="countdown-text">{{ countdown }}s</span>
          <button class="btn btn-primary btn-compact" :disabled="loading" @click="loadAll">
            刷新
          </button>
        </div>
      </div>

      <!-- Credential + account -->
      <div class="card cred-bar">
        <div class="ctrl">
          <label>使用凭据</label>
          <select v-model="selectedCredentialId" @change="loadAccountAndOrders">
            <option v-for="c in credentials" :key="c.id" :value="c.id" :disabled="!c.enabled">
              {{ c.label }} ({{ c.env }}) · …{{ c.api_key.slice(-6) }}{{ c.enabled ? '' : ' [已禁用]' }}
            </option>
          </select>
        </div>
        <div class="env-badge" :class="'env-' + currentEnv">
          {{ currentEnv === 'mainnet' ? '⚠️ 实盘' : '🧪 测试网' }}
        </div>
        <div v-if="account" class="account-inline">
          <span>余额 <strong>{{ fmt(account.total_wallet_balance) }}</strong></span>
          <span>可用 <strong>{{ fmt(account.available_balance) }}</strong></span>
          <span :class="account.total_unrealized_pnl >= 0 ? 'clr-positive' : 'clr-negative'">
            未实现盈亏 <strong>{{ pnlSign(account.total_unrealized_pnl) }}{{ fmt(account.total_unrealized_pnl) }}</strong>
          </span>
        </div>
      </div>

      <div v-if="errorMsg" class="error-bar">{{ errorMsg }}</div>

      <div v-if="recoveryAlert" class="recovery-banner" :class="recoveryAlert.level">
        <span class="recovery-text">{{ recoveryAlert.text }}</span>
        <button class="recovery-close" @click="recoveryAlert = null">×</button>
      </div>

      <!-- Chart -->
      <div class="chart-card card">
        <div ref="chartContainer" class="chart-container"></div>
      </div>

      <!-- Snapshots -->
      <div class="snapshot-row" v-if="klPayload">
        <div class="snapshot-card card" :class="{ live: current }">
          <div class="snapshot-header">
            <span class="snapshot-title">当前K线（形成中）</span>
            <span v-if="current" class="badge badge-warning">未收盘</span>
            <span v-else class="badge">无</span>
          </div>
          <div v-if="current" class="ohlc-grid">
            <div><span class="ohlc-label">O</span><span>{{ fmt(current.open) }}</span></div>
            <div><span class="ohlc-label">H</span><span class="clr-positive">{{ fmt(current.high) }}</span></div>
            <div><span class="ohlc-label">L</span><span class="clr-negative">{{ fmt(current.low) }}</span></div>
            <div><span class="ohlc-label">C</span><span :class="current.close >= current.open ? 'clr-positive' : 'clr-negative'">{{ fmt(current.close) }}</span></div>
          </div>
          <ClassificationChain v-if="current?.classification" :classification="current.classification" :enabled="enabledLevels" />
        </div>
        <div class="snapshot-card card">
          <div class="snapshot-header">
            <span class="snapshot-title">上一根已收盘K线</span>
            <span class="badge badge-success">已收盘</span>
          </div>
          <div v-if="lastClosed" class="ohlc-grid">
            <div><span class="ohlc-label">O</span><span>{{ fmt(lastClosed.open) }}</span></div>
            <div><span class="ohlc-label">H</span><span class="clr-positive">{{ fmt(lastClosed.high) }}</span></div>
            <div><span class="ohlc-label">L</span><span class="clr-negative">{{ fmt(lastClosed.low) }}</span></div>
            <div><span class="ohlc-label">C</span><span :class="lastClosed.close >= lastClosed.open ? 'clr-positive' : 'clr-negative'">{{ fmt(lastClosed.close) }}</span></div>
          </div>
          <ClassificationChain v-if="lastClosed?.classification" :classification="lastClosed.classification" :enabled="enabledLevels" />
        </div>
      </div>

      <!-- Trade form + patterns -->
      <div class="main-row">
        <div class="card trade-card">
          <TradeForm
            ref="tradeFormRef"
            :symbol="symbol"
            :credential-id="selectedCredentialId"
            :submitting="submitting"
            :computing="planComputing"
            @open-confirm="onOpenConfirm"
            @compute-plan="onComputePlan"
          />
          <div v-if="planResult" class="plan-summary">
            <span v-if="!planResult.structure_found" class="plan-warn">未找到结构，已用 ATR 兜底</span>
            <div class="plan-stat"><label>结构点</label><b>{{ planResult.structure ? planResult.structure.price : '—' }}</b></div>
            <div class="plan-stat"><label>止损</label><b>{{ planResult.stop_price }}</b></div>
            <div class="plan-stat"><label>止盈</label><b>{{ planResult.take_profit_price }}</b></div>
            <div class="plan-stat"><label>数量</label><b>{{ planResult.quantity }}</b></div>
            <div class="plan-stat"><label>风险额</label><b>{{ Number(planResult.risk_amount).toFixed(2) }}</b></div>
            <div class="plan-stat"><label>所需保证金</label><b>{{ Number(planResult.required_margin).toFixed(2) }}</b></div>
            <div v-for="(w, i) in planResult.warnings" :key="i" class="plan-warn">⚠ {{ w }}</div>
            <div v-if="!planResult.feasible" class="plan-warn">该方案不可行，请调整参数后再下单</div>
          </div>
          <div v-if="planError" class="plan-warn plan-error">计算失败：{{ planError }}</div>
        </div>
        <div class="patterns-block">
          <h2 class="section-title">
            识别到的形态
            <span class="section-count">{{ klPayload?.patterns?.length || 0 }}</span>
          </h2>
          <div v-if="!klPayload?.patterns?.length" class="empty card">尚未识别到形态</div>
          <div v-else class="pattern-list">
            <div v-for="(p, idx) in sortedPatterns" :key="idx"
                 class="pattern-card card" :class="['dir-' + p.direction]">
              <div class="pattern-icon" :class="'dir-' + p.direction">
                <span v-if="p.direction === 'bullish'">▲</span>
                <span v-else-if="p.direction === 'bearish'">▼</span>
                <span v-else>●</span>
              </div>
              <div class="pattern-body">
                <div class="pattern-name">
                  <span class="pattern-name-zh">{{ p.name_zh }}</span>
                  <span class="pattern-name-en">{{ p.name }}</span>
                </div>
                <div class="pattern-meta">
                  <span class="badge">{{ catLabel(p.category) }}</span>
                  <span class="badge" :class="p.on_closed ? 'badge-success' : 'badge-warning'">
                    {{ p.on_closed ? '已收盘' : '未收盘' }}
                  </span>
                  <span class="indices-chip">K[{{ p.indices.join(', ') }}]</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Tabs -->
      <div class="card tabs-card">
        <div class="tabs-row">
          <button v-for="t in tabs" :key="t.key" class="tab-btn"
                  :class="{ active: activeTab === t.key }" @click="activeTab = t.key">
            {{ t.label }}<span v-if="t.count != null" class="tab-count">{{ t.count }}</span>
          </button>
        </div>

        <!-- Positions -->
        <div v-show="activeTab === 'positions'">
          <table v-if="account?.positions?.length">
            <thead><tr>
              <th>币种</th><th>方向</th><th>数量</th><th>开仓均价</th>
              <th>标记价</th><th>未实现盈亏</th><th>杠杆 / 模式</th><th></th>
            </tr></thead>
            <tbody>
              <tr v-for="p in account.positions" :key="p.symbol + p.position_amt">
                <td class="col-symbol">{{ p.symbol }}</td>
                <td :class="p.position_amt > 0 ? 'clr-positive' : 'clr-negative'">
                  {{ p.position_amt > 0 ? '多' : '空' }}
                </td>
                <td>{{ Math.abs(p.position_amt) }}</td>
                <td>{{ fmt(p.entry_price) }}</td>
                <td>{{ fmt(p.mark_price) }}</td>
                <td :class="p.unrealized_pnl >= 0 ? 'clr-positive' : 'clr-negative'">
                  {{ pnlSign(p.unrealized_pnl) }}{{ fmt(p.unrealized_pnl) }}
                </td>
                <td>×{{ p.leverage }} / {{ p.margin_type }}</td>
                <td class="row-actions">
                  <button class="btn btn-sm btn-danger" @click="onClosePosition(p)">平仓</button>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-else class="tab-empty">无持仓</div>
        </div>

        <!-- Open orders -->
        <div v-show="activeTab === 'open'">
          <table v-if="openOrders.length">
            <thead><tr>
              <th>订单ID</th><th>币种</th><th>方向</th><th>类型</th>
              <th>数量</th><th>价格</th><th>触发价</th><th>状态</th><th></th>
            </tr></thead>
            <tbody>
              <tr v-for="o in openOrders" :key="o.orderId">
                <td><code>{{ o.orderId }}</code></td>
                <td>{{ o.symbol }}</td>
                <td :class="o.side === 'BUY' ? 'clr-positive' : 'clr-negative'">{{ o.side === 'BUY' ? '买' : '卖' }}</td>
                <td>{{ orderTypeLabel(o.type) }}</td>
                <td>{{ o.origQty }}</td>
                <td>{{ o.price && parseFloat(o.price) > 0 ? fmt(o.price) : '-' }}</td>
                <td>{{ o.stopPrice && parseFloat(o.stopPrice) > 0 ? fmt(o.stopPrice) : '-' }}</td>
                <td>{{ o.status }}</td>
                <td class="row-actions">
                  <button class="btn btn-sm" @click="onCancelOrder(o)">取消</button>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-else class="tab-empty">无待成交委托单</div>
        </div>

        <!-- Binance history -->
        <div v-show="activeTab === 'history'">
          <p v-if="!histSymbol" class="hist-hint">输入币种查询历史订单</p>
          <div class="hist-toolbar">
            <input v-model="histSymbol" placeholder="BTCUSDT" class="hist-input" @keyup.enter="loadHistory" />
            <button class="btn btn-sm" @click="loadHistory" :disabled="!histSymbol">查询</button>
          </div>
          <table v-if="history.length">
            <thead><tr>
              <th>时间</th><th>币种</th><th>方向</th><th>类型</th>
              <th>数量</th><th>均价</th><th>状态</th>
            </tr></thead>
            <tbody>
              <tr v-for="o in history" :key="o.orderId">
                <td>{{ fmtTime(o.updateTime || o.time) }}</td>
                <td>{{ o.symbol }}</td>
                <td :class="o.side === 'BUY' ? 'clr-positive' : 'clr-negative'">{{ o.side }}</td>
                <td>{{ orderTypeLabel(o.type) }}</td>
                <td>{{ o.executedQty }}/{{ o.origQty }}</td>
                <td>{{ o.avgPrice && parseFloat(o.avgPrice) > 0 ? fmt(o.avgPrice) : '-' }}</td>
                <td>{{ o.status }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else-if="histSymbol" class="tab-empty">无历史订单</div>
        </div>

        <!-- Audit log -->
        <div v-show="activeTab === 'audit'">
          <table v-if="auditOrders.length">
            <thead><tr>
              <th>时间</th><th>环境</th><th>币种</th><th>方向</th><th>类型</th>
              <th>数量</th><th>限价</th><th>×</th><th>状态</th><th>错误</th>
            </tr></thead>
            <tbody>
              <tr v-for="o in auditOrders" :key="o.id">
                <td>{{ o.created_at }}</td>
                <td><span class="env-tag" :class="'env-' + o.env">{{ o.env === 'mainnet' ? '实盘' : '测试网' }}</span></td>
                <td>{{ o.symbol }}</td>
                <td :class="o.side === 'BUY' ? 'clr-positive' : 'clr-negative'">{{ o.side }}</td>
                <td>{{ orderTypeLabel(o.order_type) }}</td>
                <td>{{ o.quantity }}</td>
                <td>{{ o.price || '-' }}</td>
                <td>{{ o.leverage }}</td>
                <td>
                  <span :class="o.status === 'FAILED' ? 'clr-negative' : 'clr-positive'">{{ o.status }}</span>
                </td>
                <td class="audit-err">{{ o.error_message || '' }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="tab-empty">本地暂无下单记录</div>
        </div>
      </div>
    </template>

    <!-- Confirm modal -->
    <div v-if="confirmOpen" class="modal-mask" @click.self="confirmOpen = false">
      <div class="modal-card">
        <h3>确认下单</h3>
        <div class="confirm-line">
          <span class="confirm-label">币种</span>
          <span class="confirm-value">{{ pendingPayload.symbol }}</span>
        </div>
        <div class="confirm-line">
          <span class="confirm-label">方向 / 类型</span>
          <span class="confirm-value" :class="pendingPayload.side === 'BUY' ? 'clr-positive' : 'clr-negative'">
            {{ pendingPayload.side === 'BUY' ? '做多' : '做空' }} · {{ pendingPayload.order_type === 'MARKET' ? '市价' : '限价' }}
          </span>
        </div>
        <div class="confirm-line">
          <span class="confirm-label">数量 / 杠杆</span>
          <span class="confirm-value">{{ pendingPayload.quantity }} 张 · ×{{ pendingPayload.leverage }}</span>
        </div>
        <div v-if="pendingPayload.order_type === 'LIMIT'" class="confirm-line">
          <span class="confirm-label">限价</span>
          <span class="confirm-value">{{ pendingPayload.price }}</span>
        </div>
        <div v-if="pendingPayload.stop_loss_price" class="confirm-line">
          <span class="confirm-label">止损 (SL)</span>
          <span class="confirm-value clr-negative">{{ pendingPayload.stop_loss_price }} · 触发市价整仓平</span>
        </div>
        <div v-if="pendingPayload.take_profit_price" class="confirm-line">
          <span class="confirm-label">止盈 (TP)</span>
          <span class="confirm-value clr-positive">{{ pendingPayload.take_profit_price }} · 触发市价整仓平</span>
        </div>
        <div class="confirm-line">
          <span class="confirm-label">环境</span>
          <span class="confirm-value">
            <span class="env-badge" :class="'env-' + currentEnv">
              {{ currentEnv === 'mainnet' ? '⚠️ 实盘' : '🧪 测试网' }}
            </span>
          </span>
        </div>
        <p v-if="submitError" class="modal-error">{{ submitError }}</p>
        <div class="modal-actions">
          <button class="btn" @click="confirmOpen = false" :disabled="submitting">取消</button>
          <button class="btn btn-primary" @click="submitOrder" :disabled="submitting">
            {{ submitting ? '提交中…' : '确认提交' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { createChart, CandlestickSeries, CrosshairMode } from 'lightweight-charts'
import { api } from '../api/client.js'
import ClassificationChain from '../components/ClassificationChain.vue'
import TradeForm from '../components/TradeForm.vue'

// ---- toolbar / klines state ----

const intervals = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '3d', '1w', '1M']
const symbol = ref('BTCUSDT')
const interval = ref('4h')
const limit = ref(100)
const includeCurrent = ref(false)
const refreshSec = ref(30)
const loading = ref(false)
const errorMsg = ref('')
const countdown = ref(0)

const klPayload = ref(null)
const current = computed(() => klPayload.value?.current || null)
const lastClosed = computed(() => klPayload.value?.last_closed || null)
const sortedPatterns = computed(() => {
  const list = klPayload.value?.patterns || []
  return [...list].sort((a, b) => Math.max(...b.indices) - Math.max(...a.indices))
})

// ---- L0/L1/L2/L3 level toggles ----

const LEVELS_STORAGE_KEY = 'wohub-klines-levels'
const levelOptions = [
  { key: 'L0', label: 'L0', title: '阴/阳' },
  { key: 'L1', label: 'L1', title: '阳线 / 阴线 / 十字' },
  { key: 'L2', label: 'L2', title: '看涨 / 看跌 / 无方向' },
  { key: 'L3', label: 'L3', title: '影线' },
]
function loadEnabledLevels() {
  try {
    const v = JSON.parse(localStorage.getItem(LEVELS_STORAGE_KEY) || '{}')
    return { L0: !!v.L0, L1: !!v.L1, L2: !!v.L2, L3: !!v.L3 }
  } catch { return { L0: true, L1: true, L2: true, L3: true } }
}
const enabledLevels = ref(loadEnabledLevels())
if (!enabledLevels.value.L0 && !enabledLevels.value.L1 && !enabledLevels.value.L2 && !enabledLevels.value.L3) {
  enabledLevels.value = { L0: true, L1: true, L2: true, L3: true }
}
function toggleLevel(k) {
  enabledLevels.value = { ...enabledLevels.value, [k]: !enabledLevels.value[k] }
  try { localStorage.setItem(LEVELS_STORAGE_KEY, JSON.stringify(enabledLevels.value)) } catch {}
}

// ---- credentials / account / orders ----

const credentials = ref([])
const selectedCredentialId = ref(null)
const account = ref(null)
const openOrders = ref([])
const auditOrders = ref([])
const history = ref([])
const histSymbol = ref('')

const currentEnv = computed(() => {
  const c = credentials.value.find(c => c.id === selectedCredentialId.value)
  return c?.env || 'testnet'
})

const tabs = computed(() => [
  { key: 'positions', label: '持仓', count: account.value?.positions?.length ?? 0 },
  { key: 'open', label: '委托单', count: openOrders.value.length },
  { key: 'history', label: '历史', count: null },
  { key: 'audit', label: '审计', count: auditOrders.value.length },
])
const activeTab = ref('positions')

// ---- chart ----

const chartContainer = ref(null)
let chart = null
let candleSeries = null
let priceLines = []
let resizeObserver = null
let themeObserver = null
let refreshTimer = null

function readTheme() {
  const r = getComputedStyle(document.documentElement)
  const get = (n, d) => (r.getPropertyValue(n).trim() || d)
  return {
    text: get('--text-secondary', '#999999'),
    grid: get('--border', 'rgba(255,255,255,0.07)'),
    up: get('--success', '#66b366'),
    down: get('--danger', '#d96a6a'),
    warning: get('--warning', '#cca44d'),
  }
}

function applyChartTheme() {
  if (!chart || !candleSeries) return
  const t = readTheme()
  chart.applyOptions({
    layout: { background: { color: 'transparent' }, textColor: t.text },
    grid: { vertLines: { color: t.grid }, horzLines: { color: t.grid } },
    rightPriceScale: { borderColor: t.grid },
    timeScale: { borderColor: t.grid },
  })
  candleSeries.applyOptions({
    upColor: t.up, downColor: t.down,
    borderUpColor: t.up, borderDownColor: t.down,
    wickUpColor: t.up, wickDownColor: t.down,
  })
}

function initChart() {
  if (!chartContainer.value) return
  const t = readTheme()
  chart = createChart(chartContainer.value, {
    autoSize: true,
    layout: { background: { color: 'transparent' }, textColor: t.text },
    grid: { vertLines: { color: t.grid }, horzLines: { color: t.grid } },
    crosshair: { mode: CrosshairMode.Normal },
    rightPriceScale: { borderColor: t.grid },
    timeScale: { borderColor: t.grid, timeVisible: true, secondsVisible: false },
  })
  candleSeries = chart.addSeries(CandlestickSeries, {
    upColor: t.up, downColor: t.down,
    borderUpColor: t.up, borderDownColor: t.down,
    wickUpColor: t.up, wickDownColor: t.down,
  })

  resizeObserver = new ResizeObserver(() => {
    if (chart && chartContainer.value) {
      chart.applyOptions({ width: chartContainer.value.clientWidth })
    }
  })
  resizeObserver.observe(chartContainer.value)

  themeObserver = new MutationObserver((muts) => {
    if (muts.some(m => m.attributeName === 'data-theme')) {
      applyChartTheme()
      updateChart()
    }
  })
  themeObserver.observe(document.documentElement, { attributes: true })
}

function updateChart() {
  if (!chart || !candleSeries || !klPayload.value) return
  const data = klPayload.value.candles.map(c => ({
    time: Math.floor(c.open_time / 1000),
    open: c.open, high: c.high, low: c.low, close: c.close,
  }))
  candleSeries.setData(data)

  chart.timeScale().fitContent()
  redrawPriceLines()
}

function redrawPriceLines() {
  if (!candleSeries) return
  // Clear existing
  for (const l of priceLines) { try { candleSeries.removePriceLine(l) } catch {} }
  priceLines = []
  const t = readTheme()

  const sym = symbol.value.toUpperCase()

  // Position entry line for the current symbol
  const pos = (account.value?.positions || []).find(p => p.symbol === sym)
  if (pos && pos.position_amt !== 0) {
    priceLines.push(candleSeries.createPriceLine({
      price: pos.entry_price,
      color: pos.position_amt > 0 ? t.up : t.down,
      lineWidth: 1, lineStyle: 0, axisLabelVisible: true,
      title: `${pos.position_amt > 0 ? 'Long' : 'Short'} ${Math.abs(pos.position_amt)}`,
    }))
  }

  // Open orders for the current symbol
  for (const o of openOrders.value) {
    if (o.symbol !== sym) continue
    const isLimit = o.type === 'LIMIT'
    const isStop = o.type === 'STOP_MARKET' || o.type === 'STOP'
    const isTP = o.type === 'TAKE_PROFIT_MARKET' || o.type === 'TAKE_PROFIT'
    const price = parseFloat(o.price || o.stopPrice || 0)
    if (!price) continue
    let color = t.text, label = o.type
    if (isStop) { color = t.down; label = `SL @ ${price}` }
    else if (isTP) { color = t.up; label = `TP @ ${price}` }
    else if (isLimit) { color = t.text; label = `LIMIT ${o.side} ${o.origQty}` }

    priceLines.push(candleSeries.createPriceLine({
      price, color, lineWidth: 1, lineStyle: 2,
      axisLabelVisible: true, title: label,
    }))
  }

  // Structure pivot from the latest smart plan (dashed)
  if (planResult.value?.structure_found && planResult.value.structure) {
    priceLines.push(candleSeries.createPriceLine({
      price: planResult.value.structure.price,
      color: t.warning,
      lineWidth: 1,
      lineStyle: 2,                // dashed
      axisLabelVisible: true,
      title: `结构(${planResult.value.structure.age_bars}根前)`,
    }))
  }
}

// ---- helpers ----

function normalizeSymbol(s) {
  const v = (s || '').trim().toUpperCase()
  return v.endsWith('USDT') ? v : v + 'USDT'
}

// ---- data loading ----

async function loadCredentials() {
  try {
    const { credentials: list } = await api.listTradingCredentials()
    credentials.value = list
    if (!selectedCredentialId.value && list.length) {
      selectedCredentialId.value = (list.find(c => c.enabled) || list[0]).id
    }
  } catch { credentials.value = [] }
}

async function loadKlines() {
  if (!symbol.value) return
  loading.value = true
  errorMsg.value = ''
  try {
    const sym = normalizeSymbol(symbol.value)
    klPayload.value = await api.getKlines(sym, interval.value, limit.value, includeCurrent.value)
    await nextTick()
    updateChart()
    resetCountdown()
  } catch (e) {
    errorMsg.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function loadAccountAndOrders() {
  if (!selectedCredentialId.value) return
  planResult.value = null
  planError.value = ''
  const promises = [
    api.getTradingAccount(selectedCredentialId.value).then(d => { account.value = d }).catch(() => { account.value = null }),
    api.getOpenOrders(selectedCredentialId.value).then(d => { openOrders.value = d.orders || [] }).catch(() => { openOrders.value = [] }),
    api.getTradingOrders(20).then(d => { auditOrders.value = d.orders || [] }).catch(() => { auditOrders.value = [] }),
  ]
  await Promise.all(promises)
  redrawPriceLines()
}

async function loadAll() {
  await Promise.all([loadKlines(), loadAccountAndOrders()])
}

async function loadHistory() {
  if (!histSymbol.value || !selectedCredentialId.value) return
  try {
    const d = await api.getBinanceOrderHistory(selectedCredentialId.value, histSymbol.value.toUpperCase())
    history.value = d.orders || []
  } catch (e) {
    errorMsg.value = e.message
  }
}

// ---- actions ----

const submitting = ref(false)
const confirmOpen = ref(false)
const submitError = ref('')
const recoveryAlert = ref(null)   // { level: 'danger'|'warn'|'info', text: string }
const pendingPayload = ref({})

// ---- smart plan (read-only: structure stop + risk sizing) ----

const tradeFormRef = ref(null)
const planResult = ref(null)
const planComputing = ref(false)
const planError = ref('')

async function onComputePlan(req) {
  if (!selectedCredentialId.value || !symbol.value) return
  planComputing.value = true
  planError.value = ''
  try {
    const plan = await api.buildTradingPlan({
      credential_id: selectedCredentialId.value,
      symbol: normalizeSymbol(symbol.value),
      interval: interval.value,
      ...req,
    })
    planResult.value = plan
    tradeFormRef.value?.applyPlan(plan)   // fill quantity / SL / TP
    redrawPriceLines()                    // re-draw incl. the new structure line
  } catch (e) {
    planError.value = e.message || String(e)
  } finally {
    planComputing.value = false
  }
}

function onOpenConfirm(payload) {
  submitError.value = ''
  pendingPayload.value = payload
  confirmOpen.value = true
}

async function submitOrder() {
  submitting.value = true
  submitError.value = ''
  try {
    const res = await api.placeBracketOrder(pendingPayload.value)
    recoveryAlert.value = null
    if (res.recovery) {
      recoveryAlert.value = res.recovery.naked_position
        ? { level: 'danger',
            text: `⚠️ 止损设置失败且自动撤销未完成——可能存在无止损持仓，请立即检查持仓并手动处理！（${res.recovery.detail}）` }
        : { level: 'warn',
            text: `止损单设置失败，已自动撤销本次入场（以损定仓：无止损不持仓）。${res.recovery.detail}` }
    } else if (res.ok && res.entry?.warning) {
      recoveryAlert.value = { level: 'info', text: res.entry.warning }
    }
    if (!res.ok) {
      const errs = []
      if (!res.entry.ok) errs.push(`入场失败：${res.entry.error}`)
      if (res.stop_loss && !res.stop_loss.ok) errs.push(`止损失败：${res.stop_loss.error}`)
      if (res.take_profit && !res.take_profit.ok) errs.push(`止盈失败：${res.take_profit.error}`)
      submitError.value = errs.join(' · ') || '未知错误'
      if (res.recovery) {
        // entry was undone (or needs attention) — close the modal so the
        // banner and refreshed positions are visible
        confirmOpen.value = false
        await loadAccountAndOrders()
      }
      return
    }
    confirmOpen.value = false
    await loadAccountAndOrders()
  } catch (e) {
    submitError.value = e.message
  } finally {
    submitting.value = false
  }
}

async function onClosePosition(p) {
  if (!confirm(`确认平掉 ${p.symbol} 的 ${p.position_amt > 0 ? '多头' : '空头'} 仓位（${Math.abs(p.position_amt)} 张）？`)) return
  try {
    const res = await api.closeTradingPosition(selectedCredentialId.value, p.symbol)
    if (!res.ok) errorMsg.value = `平仓失败：${res.error}`
    await loadAccountAndOrders()
  } catch (e) {
    errorMsg.value = e.message
  }
}

async function onCancelOrder(o) {
  if (!confirm(`确认取消 ${o.symbol} 的委托单 ${o.orderId}？`)) return
  try {
    await api.cancelOpenOrder(selectedCredentialId.value, o.symbol, o.orderId)
    await loadAccountAndOrders()
  } catch (e) {
    errorMsg.value = e.message
  }
}

// ---- refresh timer ----

function resetCountdown() { countdown.value = refreshSec.value }
function startRefresh() {
  stopRefresh()
  if (refreshSec.value <= 0) return
  refreshTimer = setInterval(() => {
    if (countdown.value > 0) countdown.value--
    else if (!loading.value) loadAll()
  }, 1000)
}
function stopRefresh() { if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null } }

watch(refreshSec, () => { resetCountdown(); startRefresh() })
watch([symbol, interval, limit, includeCurrent], () => { planResult.value = null; planError.value = ''; loadKlines() })

// ---- formatting ----

function fmt(n) {
  if (n == null || isNaN(n)) return '-'
  return Number(n).toLocaleString('en-US', { maximumFractionDigits: 4 })
}
function pnlSign(n) { return n > 0 ? '+' : '' }
function fmtTime(ms) {
  if (!ms) return '-'
  const d = new Date(Number(ms))
  const pad = n => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
function catLabel(c) { return { single: '单根', double: '双根', triple: '三根' }[c] || c }
function orderTypeLabel(t) {
  return { MARKET: '市价', LIMIT: '限价', STOP_MARKET: '止损市价', TAKE_PROFIT_MARKET: '止盈市价', STOP: '止损限价', TAKE_PROFIT: '止盈限价' }[t] || t
}

// ---- lifecycle ----

onMounted(async () => {
  await loadCredentials()
  await nextTick()
  initChart()
  await loadAll()
  startRefresh()
})

onUnmounted(() => {
  stopRefresh()
  if (resizeObserver) resizeObserver.disconnect()
  if (themeObserver) themeObserver.disconnect()
  if (chart) { chart.remove(); chart = null }
})
</script>

<style scoped>
.empty-card {
  text-align: center;
  padding: 48px 32px;
}
.empty-card h3 { margin-bottom: 8px; color: var(--text-primary); }
.empty-card p { margin-bottom: 20px; color: var(--text-secondary); }

.kline-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 14px 20px;
  padding: 18px 22px;
  margin-bottom: 14px;
}
.ctrl { display: flex; align-items: center; gap: 10px; }
.ctrl label {
  font-size: 12px;
  color: var(--text-secondary);
  font-weight: 500;
  letter-spacing: 0.02em;
}
.ctrl.checkbox label {
  display: flex; align-items: center; gap: 8px; cursor: pointer;
  color: var(--text-primary); font-size: 13px;
}
.ctrl-spacer { flex: 1; }
.symbol-input { width: 140px; }
.interval-select { width: 100px; }
.limit-input { width: 90px; }
.refresh-select { width: 100px; }

.countdown-text {
  font-size: 13px;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
  min-width: 30px;
  text-align: right;
}
.refresh-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--success);
}
.refresh-dot.loading { animation: pulse 0.8s ease-in-out infinite; }
@keyframes pulse { 0%, 100% { opacity: 1 } 50% { opacity: 0.3 } }

.btn.btn-compact { padding: 8px 16px; font-size: 13px; }

.level-ctrl { gap: 10px; }
.level-chips { display: inline-flex; gap: 4px; }
.level-chip {
  padding: 4px 9px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid var(--border-strong);
  border-radius: 20px;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  transition: all var(--transition-fast);
  font-family: inherit;
}
.level-chip:hover { color: var(--text-secondary); border-color: var(--text-tertiary); }
.level-chip.active {
  background: var(--accent-subtle);
  color: var(--accent);
  border-color: var(--accent);
}

.cred-bar {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 14px 22px;
  margin-bottom: 14px;
}
.cred-bar .ctrl select { min-width: 280px; }
.env-badge {
  font-size: 12px;
  font-weight: 600;
  padding: 4px 12px;
  border-radius: 20px;
}
.env-testnet { background: var(--accent-subtle); color: var(--accent); }
.env-mainnet { background: var(--danger-subtle); color: var(--danger); }

.account-inline {
  display: flex;
  gap: 24px;
  font-size: 13px;
  color: var(--text-secondary);
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}
.account-inline strong {
  color: var(--text-primary);
  font-weight: 600;
  margin-left: 6px;
}

.error-bar {
  background: var(--danger-subtle);
  color: var(--danger);
  padding: 12px 18px;
  border-radius: var(--radius-md);
  margin-bottom: 14px;
  font-size: 13px;
}

.chart-card { padding: 12px; margin-bottom: 14px; }
.chart-container { width: 100%; height: 460px; }

.snapshot-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  margin-bottom: 14px;
}
.snapshot-card { padding: 18px 22px; }
.snapshot-card.live { border-left: 3px solid var(--accent); }
.snapshot-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.snapshot-title { font-size: 13px; color: var(--text-secondary); font-weight: 500; }
.ohlc-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 6px 16px;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
}
.ohlc-grid > div { display: flex; align-items: baseline; gap: 8px; }
.ohlc-label { font-size: 11px; color: var(--text-tertiary); font-weight: 600; }
.clr-positive { color: var(--success); }
.clr-negative { color: var(--danger); }

.main-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  margin-bottom: 14px;
}
.trade-card { padding: 22px 24px; }
.patterns-block {
  display: flex;
  flex-direction: column;
}
.section-title {
  font-size: 15px;
  font-weight: 600;
  margin: 0 0 10px;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 8px;
}
.section-count {
  font-size: 11px;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  padding: 2px 8px;
  border-radius: 20px;
}
.empty {
  padding: 24px;
  text-align: center;
  color: var(--text-tertiary);
}
.pattern-list {
  display: grid;
  grid-template-columns: 1fr;
  gap: 8px;
  max-height: 440px;
  overflow-y: auto;
}
.pattern-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-left: 3px solid transparent;
  transition: all var(--transition-fast);
}
.pattern-card.dir-bullish { border-left-color: var(--success); }
.pattern-card.dir-bearish { border-left-color: var(--danger); }
.pattern-card.dir-neutral { border-left-color: var(--text-tertiary); }
.pattern-icon { font-size: 18px; width: 24px; text-align: center; }
.pattern-icon.dir-bullish { color: var(--success); }
.pattern-icon.dir-bearish { color: var(--danger); }
.pattern-icon.dir-neutral { color: var(--text-tertiary); }
.pattern-body { flex: 1; min-width: 0; }
.pattern-name {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 4px;
}
.pattern-name-zh { font-weight: 600; font-size: 13px; color: var(--text-primary); }
.pattern-name-en { font-size: 11px; color: var(--text-tertiary); }
.pattern-meta {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
}
.pattern-meta .badge { font-size: 11px; padding: 2px 7px; }
.indices-chip { font-size: 11px; color: var(--text-tertiary); font-variant-numeric: tabular-nums; }

.tabs-card { padding: 0; overflow: hidden; }
.tabs-row {
  display: flex;
  border-bottom: 1px solid var(--border);
  padding: 0 12px;
}
.tab-btn {
  padding: 14px 18px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.tab-btn:hover { color: var(--text-primary); }
.tab-btn.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
}
.tab-count {
  font-size: 11px;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  padding: 1px 8px;
  border-radius: 20px;
}
.tab-btn.active .tab-count {
  background: var(--accent-subtle);
  color: var(--accent);
}

.tabs-card table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.tabs-card thead th {
  text-align: left;
  padding: 12px 16px;
  color: var(--text-secondary);
  font-weight: 600;
  font-size: 11px;
  border-bottom: 1px solid var(--border);
  letter-spacing: 0.03em;
}
.tabs-card tbody td {
  padding: 10px 16px;
  border-bottom: 1px solid var(--border);
  font-variant-numeric: tabular-nums;
}
.tabs-card tbody tr:hover { background: var(--bg-tertiary); }
.col-symbol { font-weight: 600; }
.row-actions { text-align: right; display: flex; gap: 6px; justify-content: flex-end; }
.btn-danger { color: var(--danger); }
.btn-danger:hover { background: var(--danger-subtle); }

.tab-empty {
  padding: 32px;
  text-align: center;
  color: var(--text-tertiary);
  font-size: 13px;
}
.hist-toolbar {
  display: flex;
  gap: 10px;
  padding: 14px 18px;
  align-items: center;
}
.hist-input { width: 180px; }
.hist-hint {
  padding: 14px 18px 0;
  color: var(--text-tertiary);
  font-size: 12px;
  margin: 0;
}
.audit-err {
  color: var(--danger);
  font-size: 12px;
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.env-tag {
  font-size: 11px;
  font-weight: 500;
  padding: 2px 8px;
  border-radius: 20px;
}
.env-tag.env-testnet { background: var(--accent-subtle); color: var(--accent); }
.env-tag.env-mainnet { background: var(--danger-subtle); color: var(--danger); }

/* ---- smart plan summary ---- */
.plan-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 18px;
  padding: 12px;
  margin-top: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--bg-secondary);
}
.plan-stat { display: flex; flex-direction: column; gap: 2px; }
.plan-stat label { font-size: 11px; color: var(--text-tertiary); }
.plan-stat b { font-size: 14px; color: var(--text-primary); font-variant-numeric: tabular-nums; }
.plan-warn { width: 100%; font-size: 12px; color: var(--warning); }
.plan-error { margin-top: 8px; }

/* ---- modal ---- */
.modal-mask {
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.55);
  backdrop-filter: blur(4px);
  display: flex; align-items: center; justify-content: center;
  z-index: 1000;
}
.modal-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 26px 28px;
  width: 100%;
  max-width: 460px;
  box-shadow: var(--shadow-lg);
}
.modal-card h3 {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 18px;
  color: var(--text-primary);
}
.confirm-line {
  display: flex;
  justify-content: space-between;
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
}
.confirm-line:last-of-type { border-bottom: none; }
.confirm-label { font-size: 13px; color: var(--text-secondary); }
.confirm-value {
  font-size: 13px;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
  font-weight: 500;
}
.modal-error {
  background: var(--danger-subtle);
  color: var(--danger);
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  margin: 12px 0;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 18px;
}

.recovery-banner {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 14px; border-radius: 8px; margin: 8px 0;
  font-size: 13px; line-height: 1.5;
}
.recovery-banner.danger { background: rgba(220, 53, 69, 0.12); border: 1px solid rgba(220, 53, 69, 0.6); color: #dc3545; font-weight: 600; }
.recovery-banner.warn   { background: rgba(255, 165, 0, 0.10);  border: 1px solid rgba(255, 165, 0, 0.5);  color: #c87f0a; }
.recovery-banner.info   { background: rgba(13, 110, 253, 0.08); border: 1px solid rgba(13, 110, 253, 0.4); color: #4a8fe7; }
.recovery-banner .recovery-text { flex: 1; }
.recovery-banner .recovery-close {
  background: none; border: none; cursor: pointer; color: inherit;
  font-size: 16px; padding: 0 4px;
}

@media (max-width: 1024px) {
  .main-row { grid-template-columns: 1fr; }
  .snapshot-row { grid-template-columns: 1fr; }
  .account-inline { display: none; }
}
</style>

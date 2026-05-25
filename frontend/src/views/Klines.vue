<template>
  <div>
    <div class="page-header">
      <h1>K线形态</h1>
      <p>Binance 永续合约 · 任意时间窗口 · 实时形态识别</p>
    </div>

    <!-- Controls -->
    <div class="kline-toolbar card">
      <div class="ctrl">
        <label>币种</label>
        <input
          v-model.trim="symbol"
          class="symbol-input"
          placeholder="BTCUSDT"
          @keyup.enter="loadData"
        />
      </div>
      <div class="ctrl">
        <label>周期</label>
        <select v-model="interval" class="interval-select">
          <option v-for="iv in intervals" :key="iv" :value="iv">{{ iv }}</option>
        </select>
      </div>
      <div class="ctrl">
        <label>根数</label>
        <input
          v-model.number="limit"
          type="number"
          min="20"
          max="500"
          class="limit-input"
        />
      </div>
      <div class="ctrl checkbox">
        <label>
          <input v-model="includeCurrent" type="checkbox" />
          <span>识别当前未收盘K线</span>
        </label>
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
      <div class="ctrl level-ctrl">
        <label>分类层级</label>
        <div class="level-chips">
          <button
            v-for="lv in levelOptions"
            :key="lv.key"
            class="level-chip"
            :class="{ active: enabledLevels[lv.key] }"
            :title="lv.title"
            @click="toggleLevel(lv.key)"
          >{{ lv.label }}</button>
        </div>
      </div>
      <div class="ctrl-spacer"></div>
      <div class="ctrl">
        <span class="refresh-dot" :class="{ loading }"></span>
        <span v-if="refreshSec > 0" class="countdown-text">{{ countdown }}s</span>
        <button class="btn btn-primary btn-compact" :disabled="loading" @click="loadData">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="23 4 23 10 17 10" />
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
          </svg>
          <span>刷新</span>
        </button>
      </div>
    </div>

    <div v-if="errorMsg" class="error-bar">{{ errorMsg }}</div>

    <!-- Quick snapshot of current and last-closed candles -->
    <div class="snapshot-row" v-if="payload">
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
          <div class="ohlc-time">{{ fmtTime(current.open_time) }} → {{ fmtTime(current.close_time) }}</div>
        </div>
        <ClassificationChain
          v-if="current?.classification"
          :classification="current.classification"
          :enabled="enabledLevels"
        />
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
          <div class="ohlc-time">{{ fmtTime(lastClosed.open_time) }} → {{ fmtTime(lastClosed.close_time) }}</div>
        </div>
        <ClassificationChain
          v-if="lastClosed?.classification"
          :classification="lastClosed.classification"
          :enabled="enabledLevels"
        />
      </div>
    </div>

    <!-- Chart -->
    <div class="chart-card card">
      <div ref="chartContainer" class="chart-container"></div>
    </div>

    <!-- Patterns -->
    <div class="patterns-section">
      <h2 class="section-title">
        识别到的形态
        <span class="section-count">{{ payload?.patterns?.length || 0 }}</span>
      </h2>
      <div v-if="!payload?.patterns?.length" class="empty card">尚未识别到形态</div>
      <div v-else class="pattern-list">
        <div
          v-for="(p, idx) in sortedPatterns"
          :key="idx"
          class="pattern-card card"
          :class="['dir-' + p.direction, { active: highlightedPattern === idx }]"
          @click="highlightPattern(idx)"
        >
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
              <span class="badge">{{ categoryLabel(p.category) }}</span>
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
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { createChart, CandlestickSeries, createSeriesMarkers, CrosshairMode } from 'lightweight-charts'
import { api } from '../api/client.js'
import ClassificationChain from '../components/ClassificationChain.vue'

const LEVELS_STORAGE_KEY = 'wohub-klines-levels'
const levelOptions = [
  { key: 'L0', label: 'L0 阴阳', title: '纯粹按 close 与 open 的关系（阴/阳）' },
  { key: 'L1', label: 'L1 实体', title: '阳线 / 阴线 / 十字（带实体大小阈值）' },
  { key: 'L2', label: 'L2 方向', title: '看涨 / 看跌 / 无方向（综合判断）' },
  { key: 'L3', label: 'L3 影线', title: '长上影 / 长下影 / 双长影 / 无显著影线' },
]

function loadEnabledLevels() {
  try {
    const raw = localStorage.getItem(LEVELS_STORAGE_KEY)
    if (raw) {
      const v = JSON.parse(raw)
      if (v && typeof v === 'object') return { L0: !!v.L0, L1: !!v.L1, L2: !!v.L2, L3: !!v.L3 }
    }
  } catch {}
  return { L0: true, L1: true, L2: true, L3: true }
}
const enabledLevels = ref(loadEnabledLevels())

function toggleLevel(key) {
  enabledLevels.value = { ...enabledLevels.value, [key]: !enabledLevels.value[key] }
  try { localStorage.setItem(LEVELS_STORAGE_KEY, JSON.stringify(enabledLevels.value)) } catch {}
}

const intervals = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '3d', '1w', '1M']

const symbol = ref('BTCUSDT')
const interval = ref('4h')
const limit = ref(100)
const includeCurrent = ref(false)
const refreshSec = ref(30)

const payload = ref(null)
const loading = ref(false)
const errorMsg = ref('')
const countdown = ref(0)
const highlightedPattern = ref(null)

const chartContainer = ref(null)
let chart = null
let candleSeries = null
let markerSet = null
let resizeObserver = null
let themeObserver = null
let refreshTimer = null

const current = computed(() => payload.value?.current || null)
const lastClosed = computed(() => payload.value?.last_closed || null)

// Most recent first (sort by the latest candle they touch)
const sortedPatterns = computed(() => {
  const list = payload.value?.patterns || []
  return [...list].sort((a, b) => Math.max(...b.indices) - Math.max(...a.indices))
})

// ---- chart ----

function readTheme() {
  // Pull live values from the WoHub design system so the chart matches the
  // rest of the UI (and follows the dark/light theme variable swap).
  const root = getComputedStyle(document.documentElement)
  const get = (name, fallback) => (root.getPropertyValue(name).trim() || fallback)
  return {
    text: get('--text-secondary', '#999999'),
    grid: get('--border', 'rgba(255,255,255,0.07)'),
    up: get('--success', '#66b366'),
    down: get('--danger', '#d96a6a'),
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
    upColor: t.up,
    downColor: t.down,
    borderUpColor: t.up,
    borderDownColor: t.down,
    wickUpColor: t.up,
    wickDownColor: t.down,
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
    upColor: t.up,
    downColor: t.down,
    borderUpColor: t.up,
    borderDownColor: t.down,
    wickUpColor: t.up,
    wickDownColor: t.down,
  })

  resizeObserver = new ResizeObserver(() => {
    if (chart && chartContainer.value) {
      chart.applyOptions({ width: chartContainer.value.clientWidth })
    }
  })
  resizeObserver.observe(chartContainer.value)

  // Re-skin the chart whenever the user flips between light and dark mode.
  themeObserver = new MutationObserver((mutations) => {
    if (mutations.some(m => m.attributeName === 'data-theme')) {
      applyChartTheme()
      updateChart() // re-paint markers with new colors
    }
  })
  themeObserver.observe(document.documentElement, { attributes: true })
}

function updateChart() {
  if (!chart || !candleSeries || !payload.value) return
  const data = payload.value.candles.map((c) => ({
    time: Math.floor(c.open_time / 1000),
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
  }))
  candleSeries.setData(data)

  // Build markers — one per pattern, attached to the latest candle it involves.
  const markers = (payload.value.patterns || []).map((p) => {
    const latestIdx = Math.max(...p.indices)
    const absIdx = data.length + latestIdx // negative -> absolute
    if (absIdx < 0 || absIdx >= data.length) return null
    const candle = data[absIdx]
    return {
      time: candle.time,
      position: p.direction === 'bearish' ? 'aboveBar' : 'belowBar',
      color: colorFor(p.direction),
      shape: p.direction === 'bearish' ? 'arrowDown' : p.direction === 'bullish' ? 'arrowUp' : 'circle',
      text: p.name_zh,
    }
  }).filter(Boolean)

  if (markerSet) {
    markerSet.setMarkers(markers)
  } else {
    markerSet = createSeriesMarkers(candleSeries, markers)
  }

  chart.timeScale().fitContent()
}

function colorFor(direction) {
  const t = readTheme()
  if (direction === 'bullish') return t.up
  if (direction === 'bearish') return t.down
  return t.text
}

function highlightPattern(idx) {
  highlightedPattern.value = idx
  const p = sortedPatterns.value[idx]
  if (!p || !chart || !payload.value) return
  const latestIdx = Math.max(...p.indices)
  const absIdx = payload.value.candles.length + latestIdx
  const candle = payload.value.candles[absIdx]
  if (!candle) return
  const t = Math.floor(candle.open_time / 1000)
  // Scroll so the highlighted candle is centered
  chart.timeScale().scrollToPosition(0, false)
  chart.timeScale().setVisibleRange({
    from: t - 30 * intervalSeconds(interval.value),
    to: t + 10 * intervalSeconds(interval.value),
  })
}

function intervalSeconds(iv) {
  const map = { m: 60, h: 3600, d: 86400, w: 604800, M: 2592000 }
  const n = parseInt(iv, 10)
  const unit = iv.slice(-1)
  return n * (map[unit] || 60)
}

// ---- data loading ----

async function loadData() {
  if (!symbol.value) return
  loading.value = true
  errorMsg.value = ''
  try {
    const sym = symbol.value.toUpperCase().endsWith('USDT')
      ? symbol.value.toUpperCase()
      : symbol.value.toUpperCase() + 'USDT'
    payload.value = await api.getKlines(sym, interval.value, limit.value, includeCurrent.value)
    highlightedPattern.value = null
    await nextTick()
    updateChart()
    resetCountdown()
  } catch (e) {
    errorMsg.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function resetCountdown() {
  countdown.value = refreshSec.value
}

function startRefresh() {
  stopRefresh()
  if (refreshSec.value <= 0) return
  refreshTimer = setInterval(() => {
    if (countdown.value > 0) {
      countdown.value--
    } else if (!loading.value) {
      loadData()
    }
  }, 1000)
}

function stopRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
}

watch(refreshSec, () => {
  resetCountdown()
  startRefresh()
})

watch([symbol, interval, limit, includeCurrent], () => {
  loadData()
})

// ---- formatting ----

function fmt(n) {
  if (n == null) return '-'
  if (n >= 1000) return n.toLocaleString('en-US', { maximumFractionDigits: 2 })
  if (n >= 1) return n.toFixed(4)
  return n.toPrecision(4)
}

function fmtTime(ms) {
  const d = new Date(ms)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function categoryLabel(cat) {
  return { single: '单根', double: '双根', triple: '三根' }[cat] || cat
}

// ---- lifecycle ----

onMounted(async () => {
  await nextTick()
  initChart()
  loadData()
  startRefresh()
})

onUnmounted(() => {
  stopRefresh()
  if (resizeObserver) resizeObserver.disconnect()
  if (themeObserver) themeObserver.disconnect()
  if (chart) {
    chart.remove()
    chart = null
  }
})
</script>

<style scoped>
.kline-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 14px 20px;
  /* tighter than the default .card (28px) — toolbars feel cramped at 28 */
  padding: 18px 22px;
  margin-bottom: 18px;
}

.ctrl {
  display: flex;
  align-items: center;
  gap: 10px;
}

.ctrl label {
  font-size: 12px;
  color: var(--text-secondary);
  font-weight: 500;
  letter-spacing: 0.02em;
}

.ctrl.checkbox label {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  color: var(--text-primary);
  font-size: 13px;
}

.ctrl-spacer { flex: 1; }

/* Match the global input padding/radius — these widths just constrain the
   horizontal extent without redefining the look. */
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
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--success);
}
.refresh-dot.loading { animation: pulse 0.8s ease-in-out infinite; }
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

/* Tighter variant of the global .btn for the inline refresh button */
.btn.btn-compact {
  padding: 8px 16px;
  font-size: 13px;
}

.level-ctrl { gap: 12px; }
.level-chips {
  display: inline-flex;
  gap: 6px;
}
.level-chip {
  padding: 5px 11px;
  font-size: 12px;
  font-weight: 500;
  border: 1px solid var(--border-strong);
  border-radius: 20px;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  transition: all var(--transition-fast);
  font-family: inherit;
  letter-spacing: 0.01em;
  white-space: nowrap;
}
.level-chip:hover { color: var(--text-secondary); border-color: var(--text-tertiary); }
.level-chip.active {
  background: var(--accent-subtle);
  color: var(--accent);
  border-color: var(--accent);
}

.error-bar {
  background: var(--danger-subtle);
  color: var(--danger);
  padding: 12px 18px;
  border-radius: var(--radius-md);
  margin-bottom: 18px;
  font-size: 13px;
}

.snapshot-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 18px;
}

.snapshot-card { padding: 22px; }
.snapshot-card.live { border-left: 3px solid var(--accent); }

.snapshot-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}

.snapshot-title {
  font-size: 13px;
  color: var(--text-secondary);
  font-weight: 500;
}

.ohlc-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px 18px;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
}
.ohlc-grid > div {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.ohlc-label {
  font-size: 11px;
  color: var(--text-tertiary);
  font-weight: 600;
  letter-spacing: 0.04em;
}
.ohlc-time {
  grid-column: 1 / -1;
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 6px;
}

.clr-positive { color: var(--success); }
.clr-negative { color: var(--danger); }

/* Chart needs less padding than a normal card so the plot fills the box */
.chart-card {
  padding: 12px;
  margin-bottom: 18px;
}

.chart-container {
  width: 100%;
  height: 480px;
}

.section-title {
  font-size: 16px;
  font-weight: 600;
  margin: 8px 0 14px;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 10px;
  letter-spacing: -0.01em;
}

.section-count {
  font-size: 12px;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  padding: 2px 10px;
  border-radius: 20px;
  font-weight: 500;
}

.empty {
  padding: 32px;
  text-align: center;
  color: var(--text-tertiary);
}

.pattern-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 14px;
}

/* Override the default .card 28px padding for these denser tiles */
.pattern-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px 20px;
  cursor: pointer;
  transition: all var(--transition-fast);
  border-left: 3px solid transparent;
}
.pattern-card:hover { transform: translateY(-1px); }
.pattern-card.dir-bullish { border-left-color: var(--success); }
.pattern-card.dir-bearish { border-left-color: var(--danger); }
.pattern-card.dir-neutral { border-left-color: var(--text-tertiary); }
.pattern-card.active {
  background: var(--accent-subtle);
  border-color: var(--accent);
}

.pattern-icon {
  font-size: 20px;
  width: 28px;
  text-align: center;
}
.pattern-icon.dir-bullish { color: var(--success); }
.pattern-icon.dir-bearish { color: var(--danger); }
.pattern-icon.dir-neutral { color: var(--text-tertiary); }

.pattern-body { flex: 1; min-width: 0; }
.pattern-name {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 6px;
}
.pattern-name-zh { font-weight: 600; font-size: 14px; color: var(--text-primary); }
.pattern-name-en { font-size: 12px; color: var(--text-tertiary); }

.pattern-meta {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
}
.pattern-meta .badge { font-size: 11px; padding: 2px 8px; }
.indices-chip {
  font-size: 11px;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
  padding: 2px 0;
}

@media (max-width: 720px) {
  .snapshot-row { grid-template-columns: 1fr; }
  .ohlc-grid { grid-template-columns: repeat(2, 1fr); }
}
</style>

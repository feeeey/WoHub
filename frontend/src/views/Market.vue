<template>
  <div>
    <div class="page-header">
      <h1>市场看板</h1>
      <p>实时资金费率与涨跌幅数据</p>
    </div>

    <!-- Tabs -->
    <div class="market-tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab-btn"
        :class="{ active: activeTab === tab.key }"
        @click="switchTab(tab.key)"
      >{{ tab.label }}</button>
      <div class="tab-spacer"></div>
      <div class="market-meta">
        <span class="refresh-dot" :class="{ loading }"></span>
        <span class="refresh-text">{{ countdown }}s</span>
      </div>
    </div>

    <!-- Errors -->
    <div v-if="errors.length" class="error-bar">
      <span v-for="(e, i) in errors" :key="i">{{ e.exchange }}: {{ e.error }}</span>
    </div>

    <!-- Search + Filter -->
    <div class="market-toolbar">
      <input
        v-model="search"
        placeholder="搜索币种..."
        class="search-input"
      />
      <select v-model="exchangeFilter" class="exchange-select">
        <option value="all">全部交易所</option>
        <option value="Binance">Binance</option>
        <option value="OKX">OKX</option>
        <option value="Bybit">Bybit</option>
        <option value="Bitget">Bitget</option>
      </select>
    </div>

    <!-- Funding Rates Table -->
    <div v-if="activeTab === 'funding'" class="table-wrap card">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>币种</th>
            <th>交易所</th>
            <th>资金费率</th>
            <th>年化</th>
            <th>标记价格</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(item, idx) in filteredData" :key="idx">
            <td class="col-rank">{{ idx + 1 }}</td>
            <td class="col-symbol">{{ item.symbol }}</td>
            <td>{{ item.exchange }}</td>
            <td :class="rateClass(item.fundingRate)">{{ formatRate(item.fundingRate) }}</td>
            <td :class="rateClass(item.fundingRate)">{{ formatAnnual(item.fundingRate) }}</td>
            <td>{{ formatPrice(item.markPrice) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="!filteredData.length && !loading" class="table-empty">暂无数据</div>
    </div>

    <!-- Gainers / Losers Table -->
    <div v-if="activeTab === 'gainers' || activeTab === 'losers'" class="table-wrap card">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>币种</th>
            <th>交易所</th>
            <th>最新价</th>
            <th>24h涨跌</th>
            <th>24h最高</th>
            <th>24h最低</th>
            <th>24h成交额</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(item, idx) in filteredData" :key="idx">
            <td class="col-rank">{{ idx + 1 }}</td>
            <td class="col-symbol">{{ item.symbol }}</td>
            <td>{{ item.exchange }}</td>
            <td>{{ formatPrice(item.lastPrice) }}</td>
            <td :class="changeClass(item.priceChangePercent)">{{ formatPercent(item.priceChangePercent) }}</td>
            <td>{{ formatPrice(item.high24h) }}</td>
            <td>{{ formatPrice(item.low24h) }}</td>
            <td>{{ formatVolume(item.volume24h) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="!filteredData.length && !loading" class="table-empty">暂无数据</div>
    </div>

    <!-- Compare Tab -->
    <div v-if="activeTab === 'compare'" class="card">
      <div class="compare-input">
        <input
          v-model="compareSymbol"
          placeholder="输入币种，如 BTC"
          @keyup.enter="doCompare"
          class="search-input"
        />
        <button class="btn btn-primary btn-sm" @click="doCompare">查询</button>
      </div>
      <table v-if="compareData.length" style="margin-top: 16px">
        <thead>
          <tr>
            <th>交易所</th>
            <th>最新价</th>
            <th>24h涨跌</th>
            <th>资金费率</th>
            <th>24h成交额</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in compareData" :key="item.exchange">
            <td>{{ item.exchange }}</td>
            <td>{{ formatPrice(item.lastPrice) }}</td>
            <td :class="changeClass(item.priceChangePercent)">{{ formatPercent(item.priceChangePercent) }}</td>
            <td :class="rateClass(item.fundingRate)">{{ formatRate(item.fundingRate) }}</td>
            <td>{{ formatVolume(item.volume24h) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api } from '../api/client.js'

const tabs = [
  { key: 'funding', label: '资金费率' },
  { key: 'gainers', label: '涨幅榜' },
  { key: 'losers', label: '跌幅榜' },
  { key: 'compare', label: '跨所对比' },
]

const activeTab = ref('funding')
const loading = ref(false)
const errors = ref([])
const search = ref('')
const exchangeFilter = ref('all')
const countdown = ref(30)
const rawData = ref([])
const compareSymbol = ref('')
const compareData = ref([])
let timer = null

const filteredData = computed(() => {
  let d = rawData.value
  if (exchangeFilter.value !== 'all') {
    d = d.filter(item => item.exchange === exchangeFilter.value)
  }
  if (search.value) {
    const q = search.value.toUpperCase()
    d = d.filter(item => item.symbol.includes(q))
  }
  return d
})

async function loadData() {
  loading.value = true
  try {
    let result
    if (activeTab.value === 'funding') result = await api.fundingRates()
    else if (activeTab.value === 'gainers') result = await api.gainers()
    else if (activeTab.value === 'losers') result = await api.losers()
    else return
    rawData.value = result.data || []
    errors.value = result.errors || []
  } catch (e) {
    errors.value = [{ exchange: 'Client', error: e.message }]
  } finally {
    loading.value = false
  }
}

async function doCompare() {
  if (!compareSymbol.value) return
  try {
    const result = await api.compare(compareSymbol.value)
    compareData.value = result.data || []
  } catch (e) {
    compareData.value = []
  }
}

function switchTab(key) {
  activeTab.value = key
  rawData.value = []
  if (key !== 'compare') loadData()
}

function startTimer() {
  countdown.value = 30
  timer = setInterval(() => {
    countdown.value--
    if (countdown.value <= 0) {
      countdown.value = 30
      if (activeTab.value !== 'compare') loadData()
    }
  }, 1000)
}

function formatRate(rate) {
  if (!rate) return '0.0000%'
  return (rate >= 0 ? '+' : '') + (rate * 100).toFixed(4) + '%'
}

function formatAnnual(rate) {
  if (!rate) return '0.00%'
  const annual = rate * 3 * 365 * 100
  return (annual >= 0 ? '+' : '') + annual.toFixed(2) + '%'
}

function formatPercent(pct) {
  if (pct == null) return '-'
  return (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%'
}

function formatPrice(p) {
  if (!p) return '-'
  if (p >= 1000) return p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (p >= 1) return p.toFixed(4)
  return p.toPrecision(4)
}

function formatVolume(v) {
  if (!v) return '-'
  if (v >= 1e9) return (v / 1e9).toFixed(2) + 'B'
  if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M'
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K'
  return v.toFixed(0)
}

function rateClass(rate) {
  if (!rate) return ''
  return rate > 0 ? 'clr-positive' : rate < 0 ? 'clr-negative' : ''
}

function changeClass(pct) {
  if (pct == null) return ''
  return pct > 0 ? 'clr-positive' : pct < 0 ? 'clr-negative' : ''
}

onMounted(() => {
  loadData()
  startTimer()
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.market-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 20px;
}

.tab-btn {
  padding: 8px 18px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  border-radius: var(--radius-sm);
  transition: all var(--transition-fast);
}

.tab-btn:hover {
  color: var(--text-primary);
  background: var(--bg-tertiary);
}

.tab-btn.active {
  color: var(--accent);
  background: var(--accent-subtle);
}

.tab-spacer { flex: 1; }

.market-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-tertiary);
  font-size: 13px;
}

.refresh-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--success);
}

.refresh-dot.loading {
  animation: pulse 0.8s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

.error-bar {
  background: var(--danger-subtle);
  color: var(--danger);
  border-radius: var(--radius-sm);
  padding: 10px 16px;
  margin-bottom: 16px;
  font-size: 13px;
  display: flex;
  gap: 16px;
}

.market-toolbar {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}

.search-input {
  max-width: 280px;
}

.exchange-select {
  max-width: 180px;
}

.table-wrap {
  overflow-x: auto;
  padding: 0;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

thead th {
  text-align: left;
  padding: 12px 16px;
  color: var(--text-secondary);
  font-weight: 600;
  font-size: 12px;
  letter-spacing: 0.03em;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}

tbody td {
  padding: 10px 16px;
  border-bottom: 1px solid var(--border-subtle, var(--border));
  white-space: nowrap;
}

tbody tr:hover {
  background: var(--bg-tertiary);
}

.col-rank {
  color: var(--text-tertiary);
  width: 40px;
}

.col-symbol {
  font-weight: 600;
}

.clr-positive { color: var(--success); }
.clr-negative { color: var(--danger); }

.table-empty {
  text-align: center;
  padding: 40px;
  color: var(--text-tertiary);
}

.compare-input {
  display: flex;
  gap: 12px;
  align-items: center;
}

.compare-input .search-input {
  max-width: 240px;
}
</style>

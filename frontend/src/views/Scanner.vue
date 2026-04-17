<template>
  <div>
    <div class="page-header">
      <h1>筛选器</h1>
      <p>选择指标与时间周期，立即扫描查看结果</p>
    </div>

    <!-- Scan Config -->
    <div class="card" style="margin-bottom: 24px">
      <div class="form-group">
        <label>关注列表</label>
        <select v-model="watchlistId">
          <option :value="0">请选择</option>
          <option v-for="(id, name) in watchlists" :key="id" :value="id">{{ name }}</option>
        </select>
      </div>

      <div class="form-group">
        <label>指标（多选）</label>
        <div class="checkbox-grid">
          <label v-for="s in screeners" :key="s.screener_name" class="checkbox-item">
            <input type="checkbox" :value="s" v-model="selectedScreeners" />
            {{ s.label }}
          </label>
        </div>
      </div>

      <div class="form-group">
        <label>时间周期（多选）</label>
        <div class="checkbox-grid">
          <label v-for="r in allResolutions" :key="r" class="checkbox-item">
            <input type="checkbox" :value="r" v-model="selectedResolutions" />
            {{ r }}
          </label>
        </div>
      </div>

      <div v-if="selectedScreeners.length > 1" class="form-group">
        <label>触发信号数（≥N 个指标命中）</label>
        <input type="number" v-model.number="overlapThreshold" min="2" :max="selectedScreeners.length" style="max-width: 120px" />
      </div>

      <div class="form-actions">
        <button class="btn btn-primary" @click="runScan" :disabled="scanning || !watchlistId || !selectedScreeners.length || !selectedResolutions.length">
          {{ scanning ? '扫描中...' : '开始扫描' }}
        </button>
        <span v-if="scanTime" class="scan-time">耗时 {{ scanTime }}s</span>
      </div>
      <p v-if="scanError" class="scan-error">{{ scanError }}</p>
    </div>

    <!-- Results -->
    <div v-if="result" class="card">
      <!-- Summary -->
      <div class="result-summary">
        <div class="summary-item">
          <span class="summary-value">{{ result.total_unique }}</span>
          <span class="summary-label">标的命中</span>
        </div>
        <div class="summary-item">
          <span class="summary-value">{{ result.total_signals }}</span>
          <span class="summary-label">信号总数</span>
        </div>
        <div class="summary-item">
          <span class="summary-value">{{ result.results.length }}</span>
          <span class="summary-label">筛选任务</span>
        </div>
      </div>

      <!-- Per-screener counts -->
      <div class="screener-counts">
        <div v-for="r in result.results" :key="r.label + r.resolution" class="count-chip">
          {{ r.label }}({{ r.resolution }})
          <span class="count-num">{{ r.count }}</span>
        </div>
      </div>

      <!-- Signals by timeframe -->
      <div v-for="(sigs, res) in result.signals_by_res" :key="res" class="timeframe-section">
        <div class="timeframe-header" @click="toggleRes(res)">
          <span class="timeframe-title">{{ res }}</span>
          <span class="timeframe-count">{{ Object.keys(sigs).length }} 个标的</span>
          <span class="timeframe-arrow">{{ expandedRes[res] ? '▾' : '▸' }}</span>
        </div>
        <div v-if="expandedRes[res]" class="signal-list">
          <div v-for="(labels, sym) in sortedSignals(sigs)" :key="sym" class="signal-row">
            <span class="signal-symbol">{{ cleanSymbol(sym) }}</span>
            <span class="signal-labels">{{ labels.join(' · ') }}</span>
          </div>
        </div>
      </div>

      <!-- No signals -->
      <div v-if="result.total_signals === 0" class="no-signals">
        筛选完成，当前无命中信号
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api/client.js'

const screeners = ref([])
const watchlists = ref({})
const allResolutions = ['5m', '15m', '30m', '1h', '4h', '1d', '1w']

const watchlistId = ref(0)
const selectedScreeners = ref([])
const selectedResolutions = ref(['1h'])
const overlapThreshold = ref(2)

const scanning = ref(false)
const scanError = ref('')
const scanTime = ref(null)
const result = ref(null)
const expandedRes = ref({})

function cleanSymbol(sym) {
  return sym.replace('BINANCE:', '').replace('.P', '')
}

function sortedSignals(sigs) {
  return Object.entries(sigs)
    .sort((a, b) => b[1].length - a[1].length)
    .reduce((acc, [k, v]) => { acc[k] = v; return acc }, {})
}

function toggleRes(res) {
  expandedRes.value[res] = !expandedRes.value[res]
}

async function runScan() {
  scanning.value = true
  scanError.value = ''
  result.value = null
  scanTime.value = null
  const start = Date.now()

  try {
    result.value = await api.runScan({
      screeners: selectedScreeners.value.map(s => ({
        folder_type: s.folder_type,
        screener_name: s.screener_name,
        label: s.label,
      })),
      resolutions: selectedResolutions.value,
      watchlist_id: watchlistId.value,
      overlap_threshold: overlapThreshold.value,
    })
    // Auto-expand all timeframes
    for (const res of Object.keys(result.value.signals_by_res || {})) {
      expandedRes.value[res] = true
    }
  } catch (e) {
    scanError.value = '扫描失败: ' + e.message
  } finally {
    scanning.value = false
    scanTime.value = ((Date.now() - start) / 1000).toFixed(1)
  }
}

onMounted(async () => {
  try { screeners.value = await api.getScreeners() } catch {}
  try {
    const res = await api.getWatchlists()
    if (res.ok) watchlists.value = res.watchlists
  } catch {}
})
</script>

<style scoped>
.form-group { margin-bottom: 16px; }
.form-group label { display: block; margin-bottom: 6px; color: var(--text-secondary); font-size: 13px; font-weight: 600; }
.form-actions { display: flex; align-items: center; gap: 16px; margin-top: 8px; }

.checkbox-grid { display: flex; flex-wrap: wrap; gap: 12px; }
.checkbox-item {
  display: flex; align-items: center; gap: 6px;
  font-size: 14px; color: var(--text-primary); cursor: pointer; font-weight: 400 !important;
}
.checkbox-item input[type="checkbox"] { width: auto; accent-color: var(--accent); }

.scan-error { color: var(--danger); font-size: 13px; margin-top: 12px; }
.scan-time { color: var(--text-tertiary); font-size: 13px; }

.result-summary {
  display: flex; gap: 32px; margin-bottom: 20px;
  padding-bottom: 16px; border-bottom: 1px solid var(--border);
}
.summary-item { display: flex; flex-direction: column; align-items: center; }
.summary-value { font-size: 28px; font-weight: 700; color: var(--accent); }
.summary-label { font-size: 12px; color: var(--text-tertiary); margin-top: 2px; }

.screener-counts { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; }
.count-chip {
  display: flex; align-items: center; gap: 6px;
  padding: 4px 12px; border-radius: 20px;
  background: var(--bg-primary); font-size: 13px; color: var(--text-secondary);
}
.count-num {
  background: var(--accent); color: #fff; font-size: 11px; font-weight: 600;
  padding: 1px 7px; border-radius: 10px;
}

.timeframe-section { margin-bottom: 4px; }
.timeframe-header {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 14px; background: var(--bg-primary); border-radius: var(--radius-sm);
  cursor: pointer; font-size: 14px;
}
.timeframe-header:hover { background: var(--bg-secondary); }
.timeframe-title { font-weight: 600; }
.timeframe-count { color: var(--text-tertiary); font-size: 13px; }
.timeframe-arrow { margin-left: auto; color: var(--text-tertiary); font-size: 11px; }

.signal-list { padding: 8px 14px; }
.signal-row {
  display: flex; align-items: center; gap: 12px;
  padding: 4px 0; font-size: 13px;
  border-bottom: 1px solid var(--border-subtle, var(--border));
}
.signal-row:last-child { border-bottom: none; }
.signal-symbol { font-weight: 600; min-width: 120px; }
.signal-labels { color: var(--text-tertiary); }

.no-signals {
  text-align: center; padding: 32px; color: var(--text-tertiary); font-size: 14px;
}
</style>

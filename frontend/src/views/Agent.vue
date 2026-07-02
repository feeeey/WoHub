<template>
  <div>
    <div class="page-header">
      <h1>Agent 复盘</h1>
      <p>LLM 决策审计与结果追踪</p>
    </div>

    <div v-if="errorMsg" class="error-bar">{{ errorMsg }}</div>

    <!-- Runs list card -->
    <div class="card" style="margin-bottom: 24px">
      <div class="runs-toolbar">
        <span class="runs-title">运行记录</span>
        <div class="toolbar-right">
          <span class="countdown-text" v-if="countdown > 0">{{ countdown }}s 后刷新</span>
          <button class="btn btn-sm" :disabled="loading" @click="manualRefresh">
            {{ loading ? '加载中…' : '刷新' }}
          </button>
        </div>
      </div>

      <!-- Empty state -->
      <div v-if="!loading && runs.length === 0" class="empty-state-inline">
        暂无 Agent 运行记录——在任务的 actions 中勾选「Agent 裁决」并在设置页启用 Agent
      </div>

      <!-- Runs table -->
      <div v-else class="table-wrap">
        <table class="runs-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>任务</th>
              <th>状态</th>
              <th>模型</th>
              <th>Tokens</th>
              <th>裁决数</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="run in runs" :key="run.id">
              <tr class="run-row" :class="{ 'run-row-selected': expandedRunId === run.id }"
                  @click="toggleExpand(run.id)">
                <td class="col-time">{{ formatTime(run.started_at) }}</td>
                <td class="col-task">{{ run.task_name || '-' }}</td>
                <td>
                  <span class="badge" :class="statusBadge(run.status)">
                    {{ statusLabel(run.status) }}
                  </span>
                </td>
                <td class="col-model">{{ run.model || '-' }}</td>
                <td class="col-tokens">{{ tokenLabel(run) }}</td>
                <td class="col-count">{{ run.decision_count ?? '-' }}</td>
                <td class="col-ops">
                  <span class="expand-hint">{{ expandedRunId === run.id ? '收起' : '展开' }}</span>
                </td>
              </tr>

              <!-- Expanded detail row -->
              <tr v-if="expandedRunId === run.id" class="detail-row">
                <td colspan="7" class="detail-cell">
                  <div class="detail-wrap">
                    <!-- Loading detail -->
                    <div v-if="detailLoading" class="detail-loading">加载明细中…</div>

                    <!-- Error run -->
                    <div v-else-if="run.status === 'failed'" class="failed-block">
                      <div class="error-text">{{ currentDetail?.error || run.error || '未知错误' }}</div>
                      <button class="btn btn-sm" :disabled="rerunning" @click.stop="doRerun(run.id)">
                        {{ rerunning ? '重跑中…' : '重跑' }}
                      </button>
                    </div>

                    <!-- Decisions -->
                    <div v-if="currentDetail && currentDetail.decisions && currentDetail.decisions.length"
                         class="decisions-grid">
                      <div v-for="d in currentDetail.decisions" :key="d.id" class="decision-card">
                        <!-- Header -->
                        <div class="decision-header">
                          <span class="decision-sym">{{ d.symbol }} <span class="decision-tf">@{{ d.timeframe }}</span></span>
                          <span class="badge direction-badge" :class="directionBadge(d.direction)">
                            {{ directionLabel(d.direction) }}
                          </span>
                          <span class="confidence-tag">置信度 {{ confidencePct(d.confidence) }}</span>
                        </div>

                        <!-- Reasons -->
                        <div v-if="d.reasons" class="reasons-block">
                          <div v-for="(r, i) in d.reasons" :key="i" class="reason-line">· {{ r }}</div>
                        </div>

                        <!-- Factors table -->
                        <table v-if="d.factors && Object.keys(d.factors).length" class="factors-table">
                          <tbody>
                            <tr v-for="(val, key) in d.factors" :key="key">
                              <td class="factor-key">{{ key }}</td>
                              <td class="factor-val">{{ val }}</td>
                            </tr>
                          </tbody>
                        </table>

                        <!-- Outcome row -->
                        <div class="outcome-row">
                          <span class="outcome-label">结果：</span>
                          <span class="outcome-cell" :class="changeClass(d.change_1h, d.direction)">
                            1h {{ fmtChange(d.change_1h) }}
                          </span>
                          <span class="outcome-sep">/</span>
                          <span class="outcome-cell" :class="changeClass(d.change_4h, d.direction)">
                            4h {{ fmtChange(d.change_4h) }}
                          </span>
                          <span class="outcome-sep">/</span>
                          <span class="outcome-cell" :class="changeClass(d.change_24h, d.direction)">
                            24h {{ fmtChange(d.change_24h) }}
                          </span>
                        </div>

                        <!-- Rating + action row -->
                        <div class="decision-footer">
                          <div class="rating-group">
                            <span class="rating-label">评分</span>
                            <button class="rating-btn"
                                    :class="{ 'rating-active-pos': d.human_rating === 1 }"
                                    @click.stop="rate(d, 1)" title="正确">👍</button>
                            <button class="rating-btn"
                                    :class="{ 'rating-active-neg': d.human_rating === 0 }"
                                    @click.stop="rate(d, 0)" title="错误">👎</button>
                            <button class="rating-btn"
                                    :class="{ 'rating-active-neu': d.human_rating === -1 }"
                                    @click.stop="rate(d, -1)" title="不确定">❓</button>
                          </div>
                          <button v-if="d.direction !== 'skip'"
                                  class="btn btn-sm adopt-btn"
                                  @click.stop="goTrade(d)">
                            去下单
                          </button>
                        </div>

                        <!-- Tool trace -->
                        <details class="trace-details" v-if="currentDetail.trace">
                          <summary class="trace-summary">工具轨迹</summary>
                          <div v-if="currentDetail.trace.reused && currentDetail.trace.reused.length"
                               class="trace-reused">
                            复用裁决 #{{ currentDetail.trace.reused.join(', #') }}
                          </div>
                          <div v-if="currentDetail.trace.steps && currentDetail.trace.steps.length"
                               class="trace-steps">
                            <div v-for="(step, si) in currentDetail.trace.steps" :key="si" class="trace-step">
                              <span class="trace-tool">{{ step.tool }}</span>
                              <pre class="trace-args">{{ truncate(JSON.stringify(step.args), 200) }}</pre>
                              <pre class="trace-result">{{ truncate(String(step.result ?? ''), 300) }}</pre>
                            </div>
                          </div>
                        </details>
                      </div>
                    </div>

                    <!-- No decisions yet -->
                    <div v-else-if="currentDetail && !detailLoading && run.status !== 'failed'"
                         class="no-decisions">
                      暂无裁决记录
                    </div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client.js'

const router = useRouter()

const runs = ref([])
const loading = ref(false)
const errorMsg = ref('')
const countdown = ref(0)
const expandedRunId = ref(null)
const currentDetail = ref(null)
const detailLoading = ref(false)
const rerunning = ref(false)

const REFRESH_SEC = 10
let refreshTimer = null

// ---- data loading ----

async function loadRuns() {
  loading.value = true
  errorMsg.value = ''
  try {
    runs.value = await api.listAgentRuns(50)
    countdown.value = REFRESH_SEC
  } catch (e) {
    errorMsg.value = '加载失败: ' + e.message
  } finally {
    loading.value = false
  }
}

async function loadDetail(id) {
  detailLoading.value = true
  currentDetail.value = null
  try {
    currentDetail.value = await api.getAgentRun(id)
  } catch (e) {
    errorMsg.value = '加载明细失败: ' + e.message
  } finally {
    detailLoading.value = false
  }
}

function manualRefresh() {
  countdown.value = REFRESH_SEC
  loadRuns()
}

// ---- countdown timer (Trade.vue pattern) ----

function startTimer() {
  stopTimer()
  refreshTimer = setInterval(() => {
    if (countdown.value > 0) {
      countdown.value--
    } else if (!loading.value) {
      loadRuns()
    }
  }, 1000)
}

function stopTimer() {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
}

// ---- expand / collapse ----

async function toggleExpand(id) {
  if (expandedRunId.value === id) {
    expandedRunId.value = null
    currentDetail.value = null
    return
  }
  expandedRunId.value = id
  await loadDetail(id)
}

// ---- rerun ----

async function doRerun(id) {
  rerunning.value = true
  try {
    await api.rerunAgentRun(id)
    await loadRuns()
  } catch (e) {
    errorMsg.value = '重跑失败: ' + e.message
  } finally {
    rerunning.value = false
  }
}

// ---- rating ----

async function rate(decision, rating) {
  try {
    await api.rateAgentDecision(decision.id, rating)
    decision.human_rating = rating
  } catch (e) {
    errorMsg.value = '评分失败: ' + e.message
  }
}

// ---- go trade ----

function goTrade(d) {
  router.push({ path: '/trade', query: { symbol: d.symbol, direction: d.direction } })
}

// ---- formatters ----

function formatTime(t) {
  if (!t) return '-'
  return t.replace('T', ' ').substring(0, 16)
}

function tokenLabel(run) {
  const i = run.tokens_in ?? 0
  const o = run.tokens_out ?? 0
  if (!i && !o) return '-'
  return `${i}+${o}`
}

function statusLabel(s) {
  const m = { queued: '排队', running: '运行中', done: '完成', failed: '失败' }
  return m[s] || s
}

function statusBadge(s) {
  if (s === 'done') return 'badge-success'
  if (s === 'failed') return 'badge-danger'
  if (s === 'running') return 'badge-accent'
  return 'badge-muted'
}

function directionLabel(d) {
  if (d === 'long') return '📈 做多'
  if (d === 'short') return '📉 做空'
  return '⏭ 跳过'
}

function directionBadge(d) {
  if (d === 'long') return 'dir-long'
  if (d === 'short') return 'dir-short'
  return 'dir-skip'
}

function confidencePct(v) {
  if (v == null) return '-'
  return (v * 100).toFixed(0) + '%'
}

function fmtChange(v) {
  if (v == null) return '-'
  return (v > 0 ? '+' : '') + v.toFixed(2) + '%'
}

// Direction-aware coloring:
// long  → up=green, down=red
// short → up=red,   down=green
// skip/null → no color
function changeClass(v, direction) {
  if (v == null || direction === 'skip' || !direction) return ''
  if (direction === 'long') return v > 0 ? 'clr-positive' : v < 0 ? 'clr-negative' : ''
  if (direction === 'short') return v < 0 ? 'clr-positive' : v > 0 ? 'clr-negative' : ''
  return ''
}

function truncate(str, max) {
  if (!str) return ''
  return str.length > max ? str.slice(0, max) + '…' : str
}

onMounted(() => {
  loadRuns()
  startTimer()
})

onUnmounted(() => {
  stopTimer()
})
</script>

<style scoped>
.error-bar {
  background: var(--danger-subtle);
  color: var(--danger);
  padding: 10px 16px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  margin-bottom: 16px;
}

.runs-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.runs-title {
  font-weight: 600;
  font-size: 15px;
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.countdown-text {
  font-size: 13px;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}

.empty-state-inline {
  padding: 32px 16px;
  text-align: center;
  color: var(--text-tertiary);
  font-size: 13px;
  line-height: 1.7;
}

.table-wrap {
  overflow-x: auto;
}

.runs-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.runs-table th {
  text-align: left;
  padding: 8px 12px;
  color: var(--text-secondary);
  font-weight: 600;
  font-size: 11px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}

.runs-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border-subtle, var(--border));
  vertical-align: top;
}

.run-row {
  cursor: pointer;
  transition: background var(--transition-fast);
}

.run-row:hover {
  background: var(--bg-primary);
}

.run-row-selected {
  background: var(--accent-glow);
}

.col-time {
  color: var(--text-tertiary);
  white-space: nowrap;
  font-size: 12px;
}

.col-task {
  font-weight: 500;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.col-model {
  color: var(--text-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.col-tokens {
  color: var(--text-secondary);
  font-size: 12px;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.col-count {
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.col-ops {
  white-space: nowrap;
}

.expand-hint {
  font-size: 12px;
  color: var(--accent);
}

/* Badge variants */
.badge-accent {
  background: var(--accent-subtle);
  color: var(--accent);
}

.badge-muted {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

/* Detail expansion */
.detail-row td {
  padding: 0;
  border-bottom: 2px solid var(--border-strong);
}

.detail-cell {
  background: var(--bg-primary);
}

.detail-wrap {
  padding: 20px 16px;
}

.detail-loading {
  color: var(--text-tertiary);
  font-size: 13px;
  padding: 12px 0;
}

.failed-block {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 12px 0;
  margin-bottom: 12px;
}

.error-text {
  color: var(--danger);
  font-size: 13px;
  flex: 1;
  word-break: break-all;
}

/* Decisions grid */
.decisions-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 14px;
}

.decision-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.decision-header {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.decision-sym {
  font-weight: 700;
  font-size: 14px;
}

.decision-tf {
  font-weight: 400;
  font-size: 12px;
  color: var(--text-secondary);
}

.confidence-tag {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-left: auto;
}

/* Direction badges */
.direction-badge {
  font-size: 12px;
  font-weight: 600;
}

.dir-long {
  background: var(--success-subtle);
  color: var(--success);
}

.dir-short {
  background: var(--danger-subtle);
  color: var(--danger);
}

.dir-skip {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

/* Reasons */
.reasons-block {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.6;
}

.reason-line {
  padding: 1px 0;
}

/* Factors table */
.factors-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
}

.factors-table td {
  padding: 3px 6px;
  border-bottom: 1px solid var(--border);
}

.factor-key {
  color: var(--text-tertiary);
  white-space: nowrap;
  font-weight: 600;
  width: 40%;
}

.factor-val {
  color: var(--text-secondary);
  word-break: break-all;
}

/* Outcome */
.outcome-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  flex-wrap: wrap;
}

.outcome-label {
  color: var(--text-tertiary);
}

.outcome-cell {
  font-variant-numeric: tabular-nums;
  font-weight: 500;
}

.outcome-sep {
  color: var(--text-tertiary);
}

/* Color utils */
.clr-positive { color: var(--success); }
.clr-negative { color: var(--danger); }

/* Footer: rating + adopt */
.decision-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 2px;
}

.rating-group {
  display: flex;
  align-items: center;
  gap: 4px;
}

.rating-label {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-right: 4px;
}

.rating-btn {
  background: none;
  border: 1px solid var(--border);
  border-radius: var(--radius-xs);
  padding: 3px 8px;
  font-size: 14px;
  cursor: pointer;
  transition: background var(--transition-fast), border-color var(--transition-fast);
  line-height: 1;
}

.rating-btn:hover {
  background: var(--bg-tertiary);
}

.rating-active-pos {
  background: var(--success-subtle);
  border-color: var(--success);
}

.rating-active-neg {
  background: var(--danger-subtle);
  border-color: var(--danger);
}

.rating-active-neu {
  background: var(--warning-subtle);
  border-color: var(--warning);
}

.adopt-btn {
  font-size: 12px;
}

/* Trace */
.trace-details {
  margin-top: 4px;
  border-top: 1px solid var(--border);
  padding-top: 8px;
}

.trace-summary {
  font-size: 12px;
  color: var(--accent);
  cursor: pointer;
  user-select: none;
}

.trace-reused {
  font-size: 11px;
  color: var(--text-tertiary);
  margin: 6px 0;
}

.trace-steps {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
}

.trace-step {
  background: var(--bg-primary);
  border-radius: var(--radius-xs);
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.trace-tool {
  font-size: 11px;
  font-weight: 700;
  color: var(--accent);
  font-family: monospace;
}

.trace-args,
.trace-result {
  font-size: 10px;
  font-family: monospace;
  color: var(--text-tertiary);
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
  line-height: 1.4;
}

.no-decisions {
  text-align: center;
  padding: 20px;
  color: var(--text-tertiary);
  font-size: 13px;
}
</style>

<template>
  <div>
    <div class="page-header">
      <h1>定时任务</h1>
      <p>筛选器的定时执行版本，命中信号自动推送</p>
    </div>

    <button class="btn btn-primary" @click="openCreate" style="margin-bottom: 24px">
      创建任务
    </button>

    <!-- Create/Edit Form -->
    <div v-if="showCreate" class="card" style="margin-bottom: 24px">
      <h3 style="margin-bottom: 16px">{{ editingId ? '编辑任务' : '创建任务' }}</h3>
      <form @submit.prevent="handleCreate">
        <div class="form-group">
          <label>任务名称</label>
          <input v-model="form.name" placeholder="例如：BTC 超卖信号监控" required />
        </div>

        <div class="config-section">
          <div class="form-group">
            <label>关注列表</label>
            <select v-model="form.config.watchlist_id">
              <option :value="0">请选择</option>
              <option v-for="(id, name) in watchlists" :key="id" :value="id">{{ name }}</option>
            </select>
          </div>
          <div class="form-group">
            <label>指标（多选）</label>
            <div class="checkbox-grid">
              <label v-for="s in screeners" :key="s.screener_name" class="checkbox-item">
                <input type="checkbox" :value="s" v-model="form.config.screeners" />
                {{ s.label }}
              </label>
            </div>
          </div>
          <div class="form-group">
            <label>时间周期（多选）</label>
            <div class="checkbox-grid">
              <label v-for="r in allResolutions" :key="r" class="checkbox-item">
                <input type="checkbox" :value="r" v-model="form.config.resolutions" />
                {{ r }}
              </label>
            </div>
          </div>
          <div v-if="form.config.screeners && form.config.screeners.length > 1" class="form-group">
            <label>触发信号数（同一标的需被 ≥N 个指标命中）</label>
            <input type="number" v-model.number="form.config.overlap_threshold" min="2" :max="form.config.screeners.length" style="max-width: 120px" />
          </div>
        </div>

        <div class="form-group" style="margin-top: 16px">
          <label>推送通道</label>
          <select v-model="form.channel_id">
            <option :value="null">无</option>
            <option v-for="ch in channels" :key="ch.id" :value="ch.id">{{ ch.name }}</option>
          </select>
        </div>

        <div class="form-group">
          <label>动作</label>
          <div class="checkbox-grid">
            <label class="checkbox-item">
              <input type="checkbox" v-model="form.actions" value="text_summary" /> 文字推送
            </label>
            <label class="checkbox-item">
              <input type="checkbox" v-model="form.actions" value="chart_shot" /> 截图推送
            </label>
          </div>
        </div>

        <div class="form-actions">
          <button type="submit" class="btn btn-primary">{{ editingId ? '保存修改' : '创建' }}</button>
          <button type="button" class="btn" @click="saveAndTest" :disabled="formTesting">
            {{ formTesting ? '测试中...' : '保存并测试' }}
          </button>
          <button type="button" class="btn" @click="cancelForm">取消</button>
        </div>
        <p v-if="formError" class="form-error">{{ formError }}</p>
        <div v-if="formTestResult" style="margin-top: 12px">
          <div class="test-result" :class="formTestResult.ok ? 'test-ok' : 'test-fail'">
            {{ formTestResult.ok ? '测试执行成功' : '测试失败: ' + (formTestResult.error || '') }}
          </div>
          <div v-if="formTestResult.ok && formTestResult.detail" class="test-detail">
            <div v-if="formTestResult.detail.results" class="test-detail-section">
              <strong>筛选结果：</strong>
              <span v-for="(r, i) in formTestResult.detail.results" :key="i">
                {{ r.label }}({{ r.resolution }}): {{ r.count }}个
                <span v-if="i < formTestResult.detail.results.length - 1"> | </span>
              </span>
            </div>
            <div v-if="formTestResult.detail.total_signals != null" class="test-detail-section">
              <strong>信号命中：</strong>{{ formTestResult.detail.total_signals }} 个标的
            </div>
            <div v-if="formTestResult.detail.signals && Object.keys(formTestResult.detail.signals).length" class="test-detail-section">
              <div v-for="(labels, sym) in formTestResult.detail.signals" :key="sym" class="signal-item-mini">
                {{ cleanSymbol(sym) }}
                <span class="signal-labels">{{ labels.join(' · ') }}</span>
              </div>
            </div>
          </div>
        </div>
      </form>
    </div>

    <!-- Empty State -->
    <div v-if="tasks.length === 0 && !showCreate" class="empty-state card">
      <svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <rect x="3" y="3" width="7" height="7" rx="1.5" />
        <rect x="14" y="3" width="7" height="7" rx="1.5" />
        <rect x="3" y="14" width="7" height="7" rx="1.5" />
        <rect x="14" y="14" width="7" height="7" rx="1.5" />
      </svg>
      <h3>暂无任务</h3>
      <p>创建你的第一个定时筛选任务，开始自动追踪信号。</p>
    </div>

    <!-- Task List -->
    <div v-for="t in tasks" :key="t.id" class="card task-card">
      <div class="task-header">
        <div class="task-info">
          <span class="task-name">{{ t.name }}</span>
          <span class="badge" :class="t.running ? 'badge-success' : 'badge-danger'">
            {{ t.running ? '运行中' : '已停止' }}
          </span>
          <span class="task-schedule">{{ t.schedule_desc }}</span>
        </div>
        <div class="task-actions">
          <button class="btn btn-sm" @click="editTask(t)">编辑</button>
          <button class="btn btn-sm" @click="testRun(t)" :disabled="t.testing">
            {{ t.testing ? '执行中...' : '测试' }}
          </button>
          <button v-if="!t.running" class="btn btn-sm" @click="startTask(t)">启动</button>
          <button v-else class="btn btn-sm" @click="stopTask(t)">停止</button>
          <button class="btn btn-sm" style="color: var(--danger)" @click="removeTask(t)">删除</button>
        </div>
      </div>

      <!-- Config Summary -->
      <div class="config-toggle" @click="t.showConfig = !t.showConfig">
        <span class="config-summary">
          <span v-if="t.config?.screeners?.length" class="config-tag">{{ t.config.screeners.map(s => s.label).join(' · ') }}</span>
          <span v-if="t.config?.resolutions?.length" class="config-tag">{{ t.config.resolutions.join(' ') }}</span>
          <span v-if="t.config?.overlap_threshold && t.config.screeners?.length > 1" class="config-tag">&ge;{{ t.config.overlap_threshold }}叠加</span>
        </span>
        <span class="config-arrow">{{ t.showConfig ? '&#9662;' : '&#9656;' }}</span>
      </div>
      <div v-if="t.showConfig" class="config-detail">
        <div v-if="t.config?.watchlist_id" class="config-line">
          <span class="config-label">关注列表</span>
          <span>{{ watchlistName(t.config.watchlist_id) }}</span>
        </div>
        <div v-if="t.config?.screeners?.length" class="config-line">
          <span class="config-label">指标</span>
          <span>{{ t.config.screeners.map(s => s.label).join(', ') }}</span>
        </div>
        <div v-if="t.config?.resolutions?.length" class="config-line">
          <span class="config-label">周期</span>
          <span>{{ t.config.resolutions.join(', ') }}</span>
        </div>
        <div v-if="t.config?.overlap_threshold && t.config.screeners?.length > 1" class="config-line">
          <span class="config-label">触发信号数</span>
          <span>&ge;{{ t.config.overlap_threshold }} 个指标叠加</span>
        </div>
        <div v-if="t.channel_id" class="config-line">
          <span class="config-label">推送通道</span>
          <span>{{ channelName(t.channel_id) }}</span>
        </div>
        <div class="config-line">
          <span class="config-label">动作</span>
          <span>{{ (t.actions || []).map(a => a === 'text_summary' ? '文字' : a === 'chart_shot' ? '截图' : a).join(', ') }}</span>
        </div>
      </div>

      <div v-if="t.testResult" class="test-result" :class="t.testResult.ok ? 'test-ok' : 'test-fail'">
        {{ t.testResult.ok ? '执行成功' + (t.testResult.detail?.total_signals != null ? ' -- ' + t.testResult.detail.total_signals + ' 个信号' : '') : '执行失败: ' + (t.testResult.error || '') }}
      </div>

      <!-- History -->
      <div class="history-toggle" @click="toggleHistory(t)">
        {{ t.showHistory ? '收起历史' : '查看历史' }}
        <span v-if="t.historyCount" class="history-count">{{ t.historyCount }}</span>
      </div>
      <div v-if="t.showHistory && t.history" class="history-panel">
        <div v-if="!t.history.signals.length" class="history-empty">暂无执行记录</div>
        <table v-else class="history-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>币种</th>
              <th>指标</th>
              <th>周期</th>
              <th>触发价</th>
              <th>1h</th>
              <th>4h</th>
              <th>24h</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in t.history.signals" :key="s.id">
              <td class="col-time">{{ formatTime(s.triggered_at) }}</td>
              <td class="col-symbol">{{ s.symbol }}</td>
              <td>{{ s.indicator }}</td>
              <td>{{ s.timeframe }}</td>
              <td>{{ s.price ? Number(s.price).toFixed(2) : '-' }}</td>
              <td :class="changeClass(s.change_1h)">{{ s.change_1h != null ? s.change_1h.toFixed(2) + '%' : '-' }}</td>
              <td :class="changeClass(s.change_4h)">{{ s.change_4h != null ? s.change_4h.toFixed(2) + '%' : '-' }}</td>
              <td :class="changeClass(s.change_24h)">{{ s.change_24h != null ? s.change_24h.toFixed(2) + '%' : '-' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api/client.js'

const tasks = ref([])
const channels = ref([])
const screeners = ref([])
const watchlists = ref({})
const showCreate = ref(false)
const editingId = ref(null)
const formTesting = ref(false)
const formTestResult = ref(null)
const formError = ref('')
const allResolutions = ['5m', '15m', '30m', '1h', '4h', '1d', '1w']

const defaultConfig = { watchlist_id: 0, screeners: [], resolutions: ['1h'], overlap_threshold: 2 }

const form = ref({
  name: '', type: 'watchlist_signal', schedule: '1h',
  channel_id: null, actions: ['text_summary'],
  config: { ...defaultConfig },
})

function cleanSymbol(sym) {
  return sym.replace('BINANCE:', '').replace('.P', '')
}

function watchlistName(id) {
  for (const [name, wid] of Object.entries(watchlists.value)) {
    if (wid === id) return name
  }
  return `ID:${id}`
}

function channelName(id) {
  const ch = channels.value.find(c => c.id === id)
  return ch ? ch.name : `ID:${id}`
}

function openCreate() {
  editingId.value = null
  formTestResult.value = null
  formError.value = ''
  form.value = {
    name: '', type: 'watchlist_signal', schedule: '1h',
    channel_id: null, actions: ['text_summary'],
    config: { ...defaultConfig },
  }
  showCreate.value = true
}

function editTask(t) {
  editingId.value = t.id
  formTestResult.value = null
  formError.value = ''
  form.value = {
    name: t.name,
    type: t.type || 'watchlist_signal',
    schedule: t.schedule,
    channel_id: t.channel_id,
    actions: [...(t.actions || [])],
    config: JSON.parse(JSON.stringify(t.config || defaultConfig)),
  }
  showCreate.value = true
}

function cancelForm() {
  showCreate.value = false
  editingId.value = null
}

async function loadTasks() {
  tasks.value = (await api.listTasks()).map(t => ({
    ...t, testing: false, testResult: null, showConfig: false,
    showHistory: false, history: null, historyCount: null,
  }))
}

async function handleCreate() {
  formError.value = ''
  const data = { ...form.value }
  data.type = 'watchlist_signal'
  try {
    if (editingId.value) {
      await api.updateTask(editingId.value, data)
    } else {
      await api.createTask(data)
    }
    showCreate.value = false
    editingId.value = null
    await loadTasks()
  } catch (e) {
    formError.value = '保存失败: ' + e.message
  }
}

async function startTask(t) { await api.startTask(t.id); await loadTasks() }
async function stopTask(t) { await api.stopTask(t.id); await loadTasks() }

async function testRun(t) {
  t.testing = true; t.testResult = null
  try { t.testResult = await api.testTask(t.id) }
  catch (e) { t.testResult = { ok: false, error: e.message } }
  finally { t.testing = false }
}

async function saveAndTest() {
  formTesting.value = true
  formTestResult.value = null
  formError.value = ''
  try {
    let taskId = editingId.value
    const data = { ...form.value }
    data.type = 'watchlist_signal'
    if (taskId) {
      await api.updateTask(taskId, data)
    } else {
      const created = await api.createTask(data)
      taskId = created.id
      editingId.value = taskId
    }
    formTestResult.value = await api.testTask(taskId)
    await loadTasks()
  } catch (e) {
    formTestResult.value = { ok: false, error: e.message }
  } finally {
    formTesting.value = false
  }
}

function formatTime(t) {
  if (!t) return ''
  return t.replace('T', ' ').substring(5, 16)
}

function changeClass(v) {
  if (v == null) return ''
  return v > 0 ? 'clr-positive' : v < 0 ? 'clr-negative' : ''
}

async function toggleHistory(t) {
  if (t.showHistory) { t.showHistory = false; return }
  try {
    t.history = await api.getTaskHistory(t.id)
    t.historyCount = t.history.signals.length
    t.showHistory = true
  } catch {
    t.history = { signals: [], push_logs: [] }
    t.showHistory = true
  }
}

async function removeTask(t) {
  if (!confirm(`确认删除任务 "${t.name}"？`)) return
  try {
    await api.deleteTask(t.id)
    await loadTasks()
  } catch (e) {
    alert('删除失败: ' + e.message)
  }
}

onMounted(() => {
  loadTasks()
  Promise.all([
    api.listChannels().then(r => channels.value = r).catch(() => {}),
    api.getScreeners().then(r => screeners.value = r).catch(() => {}),
    api.getWatchlists().then(r => { if (r.ok) watchlists.value = r.watchlists }).catch(() => {}),
  ])
})
</script>

<style scoped>
.form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.form-group { margin-bottom: 16px; }
.form-group label { display: block; margin-bottom: 6px; color: var(--text-secondary); font-size: 13px; font-weight: 600; }
.form-actions { display: flex; gap: 12px; margin-top: 8px; }
.form-error { color: var(--danger); font-size: 13px; margin-top: 12px; }

.config-section {
  padding: 20px;
  background: var(--bg-primary);
  border-radius: var(--radius-md);
  margin-bottom: 16px;
}

.checkbox-grid { display: flex; flex-wrap: wrap; gap: 12px; }
.checkbox-item {
  display: flex; align-items: center; gap: 6px;
  font-size: 14px; color: var(--text-primary); cursor: pointer; font-weight: 400 !important; margin-bottom: 0 !important;
}
.checkbox-item input[type="checkbox"] { width: auto; accent-color: var(--accent); }

.task-card { margin-bottom: 12px; }
.task-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.task-info { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.task-name { font-weight: 600; font-size: 15px; }
.task-schedule { color: var(--text-tertiary); font-size: 12px; }
.task-actions { display: flex; gap: 8px; flex-shrink: 0; }

.config-toggle {
  display: flex; align-items: center; justify-content: space-between;
  margin-top: 10px; padding: 6px 12px;
  background: var(--bg-primary); border-radius: var(--radius-sm);
  cursor: pointer; font-size: 13px;
}
.config-toggle:hover { background: var(--bg-secondary); }
.config-summary { display: flex; gap: 8px; flex-wrap: wrap; }
.config-tag { color: var(--text-secondary); font-size: 12px; }
.config-arrow { color: var(--text-tertiary); font-size: 11px; flex-shrink: 0; }
.config-detail {
  padding: 10px 14px; background: var(--bg-primary);
  border-radius: 0 0 var(--radius-sm) var(--radius-sm); font-size: 13px;
}
.config-line { display: flex; gap: 12px; padding: 3px 0; }
.config-label { color: var(--text-tertiary); min-width: 70px; flex-shrink: 0; }

.test-result { margin-top: 12px; padding: 8px 14px; border-radius: var(--radius-sm); font-size: 13px; }
.test-ok { background: var(--success-subtle); color: var(--success); }
.test-fail { background: var(--danger-subtle); color: var(--danger); }

.history-toggle {
  margin-top: 12px; font-size: 13px; color: var(--accent);
  cursor: pointer; display: flex; align-items: center; gap: 6px;
}
.history-toggle:hover { text-decoration: underline; }
.history-count {
  background: var(--bg-tertiary); padding: 1px 7px; border-radius: 10px;
  font-size: 11px; color: var(--text-secondary);
}
.history-panel { margin-top: 12px; overflow-x: auto; }
.history-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.history-table th {
  text-align: left; padding: 8px 10px; color: var(--text-secondary);
  font-weight: 600; font-size: 11px; border-bottom: 1px solid var(--border); white-space: nowrap;
}
.history-table td {
  padding: 6px 10px; border-bottom: 1px solid var(--border-subtle, var(--border)); white-space: nowrap;
}
.history-table .col-time { color: var(--text-tertiary); }
.history-table .col-symbol { font-weight: 600; }
.history-empty { text-align: center; padding: 20px; color: var(--text-tertiary); font-size: 13px; }

.clr-positive { color: var(--success); }
.clr-negative { color: var(--danger); }

.test-detail {
  margin-top: 8px; padding: 12px 14px; background: var(--bg-primary);
  border-radius: var(--radius-sm); font-size: 13px; line-height: 1.6;
  max-height: 300px; overflow-y: auto;
}
.test-detail-section { margin-bottom: 8px; }
.test-detail-section strong { color: var(--text-secondary); }
.signal-item-mini { padding: 2px 0; font-size: 12px; }
.signal-item-mini .signal-labels { color: var(--text-tertiary); margin-left: 8px; }
</style>

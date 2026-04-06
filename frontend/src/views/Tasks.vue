<template>
  <div>
    <div class="page-header">
      <h1>任务管理</h1>
      <p>创建和管理信号监控任务</p>
    </div>

    <button class="btn btn-primary" @click="openCreate" style="margin-bottom: 24px">
      创建任务
    </button>

    <!-- Create Form -->
    <div v-if="showCreate" class="card" style="margin-bottom: 24px">
      <h3 style="margin-bottom: 16px">创建任务</h3>
      <form @submit.prevent="handleCreate">
        <div class="form-row">
          <div class="form-group">
            <label>任务名称</label>
            <input v-model="form.name" placeholder="例如：BTC 信号监控" required />
          </div>
          <div class="form-group">
            <label>任务类型</label>
            <select v-model="form.type" @change="onTypeChange">
              <option value="watchlist_signal">关注列表信号监控</option>
              <option value="market_scan">全市场叠加扫描</option>
              <option value="anomaly_watch">异常行情监控</option>
              <option value="scheduled_shot">定时截图</option>
            </select>
          </div>
        </div>

        <!-- watchlist_signal config -->
        <div v-if="form.type === 'watchlist_signal'" class="config-section">
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
        </div>

        <!-- market_scan config -->
        <div v-if="form.type === 'market_scan'" class="config-section">
          <div class="form-group">
            <label>关注列表（扫描范围）</label>
            <select v-model="form.config.watchlist_id">
              <option :value="0">请选择</option>
              <option v-for="(id, name) in watchlists" :key="id" :value="id">{{ name }}</option>
            </select>
          </div>
          <div class="form-group">
            <label>指标组合（多选）</label>
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
          <div class="form-row">
            <div class="form-group">
              <label>叠加阈值（≥N个指标命中）</label>
              <input type="number" v-model.number="form.config.overlap_threshold" min="2" max="10" />
            </div>
            <div class="form-group">
              <label>截图阈值（≥N个信号截图）</label>
              <input type="number" v-model.number="form.config.screenshot_threshold" min="2" max="10" />
            </div>
          </div>
        </div>

        <!-- anomaly_watch config -->
        <div v-if="form.type === 'anomaly_watch'" class="config-section">
          <div class="form-row">
            <div class="form-group">
              <label>监控类型</label>
              <select v-model="form.config.monitor_type">
                <option value="price_change">涨跌幅</option>
                <option value="funding_rate">资金费率</option>
              </select>
            </div>
            <div class="form-group">
              <label>异常阈值（{{ form.config.monitor_type === 'price_change' ? '%' : '费率万分位' }}）</label>
              <input type="number" v-model.number="form.config.threshold" step="0.1" />
            </div>
          </div>
          <div class="form-group">
            <label>联动检查指标（异常出现后自动跑以下指标）</label>
            <div class="checkbox-grid">
              <label v-for="s in screeners" :key="s.screener_name" class="checkbox-item">
                <input type="checkbox" :value="s" v-model="form.config.screeners" />
                {{ s.label }}
              </label>
            </div>
          </div>
          <div class="form-group">
            <label>关注列表（联动检查范围）</label>
            <select v-model="form.config.watchlist_id">
              <option :value="0">全市场</option>
              <option v-for="(id, name) in watchlists" :key="id" :value="id">{{ name }}</option>
            </select>
          </div>
          <div class="form-group">
            <label>时间周期</label>
            <div class="checkbox-grid">
              <label v-for="r in allResolutions" :key="r" class="checkbox-item">
                <input type="checkbox" :value="r" v-model="form.config.resolutions" />
                {{ r }}
              </label>
            </div>
          </div>
        </div>

        <!-- scheduled_shot config -->
        <div v-if="form.type === 'scheduled_shot'" class="config-section">
          <div class="form-group">
            <label>标的列表（逗号分隔）</label>
            <input v-model="symbolsInput" placeholder="BTC, ETH, SOL" />
          </div>
          <div class="form-group">
            <label>截图周期</label>
            <div class="checkbox-grid">
              <label v-for="r in allResolutions" :key="r" class="checkbox-item">
                <input type="checkbox" :value="r" v-model="form.config.timeframes" />
                {{ r }}
              </label>
            </div>
          </div>
        </div>

        <!-- Common fields -->
        <div class="form-row" style="margin-top: 16px">
          <div class="form-group">
            <label>调度周期</label>
            <select v-model="form.schedule">
              <option value="5m">每5分钟</option>
              <option value="15m">每15分钟</option>
              <option value="30m">每30分钟</option>
              <option value="1h">每小时</option>
              <option value="4h">每4小时</option>
              <option value="1d">每天</option>
              <option value="1w">每周</option>
            </select>
          </div>
          <div class="form-group">
            <label>推送通道</label>
            <select v-model="form.channel_id">
              <option :value="null">无</option>
              <option v-for="ch in channels" :key="ch.id" :value="ch.id">{{ ch.name }}</option>
            </select>
          </div>
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
          <button type="submit" class="btn btn-primary">创建</button>
          <button type="button" class="btn" @click="showCreate = false">取消</button>
        </div>
      </form>
    </div>

    <!-- Task List -->
    <div v-if="tasks.length === 0 && !showCreate" class="empty-state card">
      <svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <rect x="3" y="3" width="7" height="7" rx="1.5" />
        <rect x="14" y="3" width="7" height="7" rx="1.5" />
        <rect x="3" y="14" width="7" height="7" rx="1.5" />
        <rect x="14" y="14" width="7" height="7" rx="1.5" />
      </svg>
      <h3>暂无任务</h3>
      <p>创建你的第一个监控任务，开始追踪信号。</p>
    </div>

    <div v-for="t in tasks" :key="t.id" class="card task-card">
      <div class="task-header">
        <div class="task-info">
          <span class="task-name">{{ t.name }}</span>
          <span class="badge" :class="t.running ? 'badge-success' : 'badge-danger'">
            {{ t.running ? '运行中' : '已停止' }}
          </span>
          <span class="task-type">{{ typeLabel(t.type) }}</span>
          <span class="task-schedule">{{ t.schedule_desc }}</span>
        </div>
        <div class="task-actions">
          <button class="btn btn-sm" @click="testRun(t)" :disabled="t.testing">
            {{ t.testing ? '执行中...' : '测试' }}
          </button>
          <button v-if="!t.running" class="btn btn-sm" @click="startTask(t)">启动</button>
          <button v-else class="btn btn-sm" @click="stopTask(t)">停止</button>
          <button class="btn btn-sm" style="color: var(--danger)" @click="removeTask(t)">删除</button>
        </div>
      </div>
      <div v-if="t.testResult" class="test-result" :class="t.testResult.ok ? 'test-ok' : 'test-fail'">
        {{ t.testResult.ok ? '执行成功' : '执行失败: ' + (t.testResult.error || '') }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api/client.js'

const tasks = ref([])
const channels = ref([])
const screeners = ref([])
const watchlists = ref({})
const showCreate = ref(false)
const symbolsInput = ref('')
const allResolutions = ['5m', '15m', '30m', '1h', '4h', '1d', '1w']

const defaultConfigs = {
  watchlist_signal: { watchlist_id: 0, screeners: [], resolutions: ['1h'] },
  market_scan: { watchlist_id: 0, screeners: [], resolutions: ['1h'], overlap_threshold: 2, screenshot_threshold: 3 },
  anomaly_watch: { monitor_type: 'price_change', threshold: 10, screeners: [], resolutions: ['1h'], watchlist_id: 0 },
  scheduled_shot: { symbols: [], timeframes: ['1h'] },
}

const form = ref({
  name: '', type: 'watchlist_signal', schedule: '1h',
  channel_id: null, actions: ['text_summary'],
  config: { ...defaultConfigs.watchlist_signal },
})

const TYPE_LABELS = {
  watchlist_signal: '关注列表信号',
  market_scan: '全市场扫描',
  anomaly_watch: '异常行情',
  scheduled_shot: '定时截图',
}

function typeLabel(t) { return TYPE_LABELS[t] || t }

function onTypeChange() {
  form.value.config = { ...defaultConfigs[form.value.type] }
  symbolsInput.value = ''
}

function openCreate() {
  form.value = {
    name: '', type: 'watchlist_signal', schedule: '1h',
    channel_id: null, actions: ['text_summary'],
    config: { ...defaultConfigs.watchlist_signal },
  }
  symbolsInput.value = ''
  showCreate.value = true
}

async function loadTasks() {
  tasks.value = (await api.listTasks()).map(t => ({ ...t, testing: false, testResult: null }))
}

async function loadChannels() {
  try { channels.value = await api.listChannels() } catch {}
}

async function loadScreeners() {
  try { screeners.value = await api.getScreeners() } catch {}
}

async function loadWatchlists() {
  try {
    const res = await api.getWatchlists()
    if (res.ok) watchlists.value = res.watchlists
  } catch {}
}

async function handleCreate() {
  const data = { ...form.value }
  // For scheduled_shot, parse symbols from comma-separated input
  if (data.type === 'scheduled_shot') {
    data.config.symbols = symbolsInput.value.split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
  }
  await api.createTask(data)
  showCreate.value = false
  await loadTasks()
}

async function startTask(t) { await api.startTask(t.id); await loadTasks() }
async function stopTask(t) { await api.stopTask(t.id); await loadTasks() }

async function testRun(t) {
  t.testing = true; t.testResult = null
  try { t.testResult = await api.testTask(t.id) }
  catch (e) { t.testResult = { ok: false, error: e.message } }
  finally { t.testing = false }
}

async function removeTask(t) {
  if (!confirm(`确认删除任务 "${t.name}"？`)) return
  await api.deleteTask(t.id)
  await loadTasks()
}

onMounted(() => {
  loadTasks()
  loadChannels()
  loadScreeners()
  loadWatchlists()
})
</script>

<style scoped>
.form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.form-group { margin-bottom: 16px; }
.form-group label { display: block; margin-bottom: 6px; color: var(--text-secondary); font-size: 13px; font-weight: 600; }
.form-actions { display: flex; gap: 12px; margin-top: 8px; }

.config-section {
  padding: 20px;
  background: var(--bg-primary);
  border-radius: var(--radius-md);
  margin-bottom: 16px;
}

.checkbox-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.checkbox-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  color: var(--text-primary);
  cursor: pointer;
  font-weight: 400 !important;
  margin-bottom: 0 !important;
}

.checkbox-item input[type="checkbox"] {
  width: auto;
  accent-color: var(--accent);
}

.task-card { margin-bottom: 12px; }
.task-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.task-info { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.task-name { font-weight: 600; font-size: 15px; }
.task-type { color: var(--text-tertiary); font-size: 13px; }
.task-schedule { color: var(--text-tertiary); font-size: 12px; }
.task-actions { display: flex; gap: 8px; flex-shrink: 0; }
.test-result { margin-top: 12px; padding: 8px 14px; border-radius: var(--radius-sm); font-size: 13px; }
.test-ok { background: var(--success-subtle); color: var(--success); }
.test-fail { background: var(--danger-subtle); color: var(--danger); }
</style>

<template>
  <div>
    <div class="page-header">
      <h1>任务管理</h1>
      <p>创建和管理信号监控任务</p>
    </div>

    <button class="btn btn-primary" @click="showCreate = true" style="margin-bottom: 24px">
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
            <select v-model="form.type">
              <option value="watchlist_signal">关注列表信号监控</option>
              <option value="market_scan">全市场叠加扫描</option>
              <option value="anomaly_watch">异常行情监控</option>
              <option value="scheduled_shot">定时截图</option>
            </select>
          </div>
        </div>
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
        <div class="form-group">
          <label>动作</label>
          <div style="display: flex; gap: 16px">
            <label style="font-size: 14px; display: flex; align-items: center; gap: 6px; color: var(--text-primary)">
              <input type="checkbox" v-model="form.actions" value="text_summary" /> 文字推送
            </label>
            <label style="font-size: 14px; display: flex; align-items: center; gap: 6px; color: var(--text-primary)">
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
import { ref, onMounted } from 'vue'
import { api } from '../api/client.js'

const tasks = ref([])
const channels = ref([])
const showCreate = ref(false)
const form = ref({
  name: '', type: 'watchlist_signal', schedule: '1h',
  channel_id: null, actions: ['text_summary'],
  config: {},
})

const TYPE_LABELS = {
  watchlist_signal: '关注列表信号',
  market_scan: '全市场扫描',
  anomaly_watch: '异常行情',
  scheduled_shot: '定时截图',
}

function typeLabel(t) { return TYPE_LABELS[t] || t }

async function loadTasks() {
  tasks.value = (await api.listTasks()).map(t => ({ ...t, testing: false, testResult: null }))
}

async function loadChannels() {
  try { channels.value = await api.listChannels() } catch {}
}

async function handleCreate() {
  await api.createTask(form.value)
  showCreate.value = false
  form.value = { name: '', type: 'watchlist_signal', schedule: '1h', channel_id: null, actions: ['text_summary'], config: {} }
  await loadTasks()
}

async function startTask(t) {
  await api.startTask(t.id)
  await loadTasks()
}

async function stopTask(t) {
  await api.stopTask(t.id)
  await loadTasks()
}

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

onMounted(() => { loadTasks(); loadChannels() })
</script>

<style scoped>
.form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.form-group { margin-bottom: 16px; }
.form-group label { display: block; margin-bottom: 6px; color: var(--text-secondary); font-size: 13px; font-weight: 600; }
.form-actions { display: flex; gap: 12px; margin-top: 8px; }
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

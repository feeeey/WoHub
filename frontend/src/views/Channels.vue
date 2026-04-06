<template>
  <div>
    <div class="page-header">
      <h1>推送通道</h1>
      <p>管理 Telegram、Discord 等消息推送渠道</p>
    </div>

    <button class="btn btn-primary" @click="showForm = true" style="margin-bottom: 24px">
      添加通道
    </button>

    <!-- Add/Edit Form -->
    <div v-if="showForm" class="card" style="margin-bottom: 24px">
      <h3 style="margin-bottom: 16px">{{ editing ? '编辑通道' : '添加通道' }}</h3>
      <form @submit.prevent="saveChannel">
        <div class="form-row">
          <div class="form-group">
            <label>名称</label>
            <input v-model="form.name" placeholder="例如：交易信号群" required />
          </div>
          <div class="form-group">
            <label>类型</label>
            <select v-model="form.type" :disabled="!!editing">
              <option value="telegram">Telegram</option>
              <option value="discord" disabled>Discord (即将支持)</option>
              <option value="webhook" disabled>Webhook (即将支持)</option>
            </select>
          </div>
        </div>

        <div v-if="form.type === 'telegram'">
          <div class="form-group">
            <label>Bot Token</label>
            <input v-model="form.config.bot_token" placeholder="从 @BotFather 获取" required />
          </div>
          <div class="form-group">
            <label>Chat ID</label>
            <input v-model="form.config.chat_id" placeholder="群组或用户 ID" required />
          </div>
        </div>

        <div class="form-actions">
          <button type="submit" class="btn btn-primary">{{ editing ? '保存' : '添加' }}</button>
          <button type="button" class="btn" @click="cancelForm">取消</button>
        </div>
        <p v-if="formError" class="form-error">{{ formError }}</p>
      </form>
    </div>

    <!-- Channel List -->
    <div v-if="channels.length === 0 && !showForm" class="empty-state card">
      <svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M22 2L11 13" /><path d="M22 2L15 22L11 13L2 9L22 2Z" />
      </svg>
      <h3>暂无通道</h3>
      <p>添加一个推送通道，将信号发送到 Telegram 群组。</p>
    </div>

    <div v-for="ch in channels" :key="ch.id" class="channel-card card">
      <div class="channel-header">
        <div class="channel-info">
          <span class="channel-name">{{ ch.name }}</span>
          <span class="badge" :class="ch.enabled ? 'badge-success' : 'badge-danger'">
            {{ ch.enabled ? '已启用' : '已停用' }}
          </span>
          <span class="channel-type">{{ ch.type }}</span>
        </div>
        <div class="channel-actions">
          <button class="btn btn-sm" @click="testPush(ch)" :disabled="ch.testing">
            {{ ch.testing ? '测试中...' : '测试' }}
          </button>
          <button class="btn btn-sm" @click="editChannel(ch)">编辑</button>
          <button class="btn btn-sm" @click="toggleEnabled(ch)">
            {{ ch.enabled ? '停用' : '启用' }}
          </button>
          <button class="btn btn-sm" style="color: var(--danger)" @click="removeChannel(ch)">删除</button>
        </div>
      </div>
      <div v-if="ch.testResult" class="test-result" :class="ch.testResult.ok ? 'test-ok' : 'test-fail'">
        {{ ch.testResult.ok ? '连接成功: ' + (ch.testResult.bot_name || '') : '连接失败: ' + (ch.testResult.error || '') }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api/client.js'

const channels = ref([])
const showForm = ref(false)
const editing = ref(null)
const formError = ref('')
const form = ref({
  name: '',
  type: 'telegram',
  config: { bot_token: '', chat_id: '' },
})

async function loadChannels() {
  try {
    channels.value = (await api.listChannels()).map(c => ({
      ...c,
      testing: false,
      testResult: null,
    }))
  } catch (e) {
    console.error('Failed to load channels:', e)
  }
}

function resetForm() {
  form.value = { name: '', type: 'telegram', config: { bot_token: '', chat_id: '' } }
  editing.value = null
  formError.value = ''
}

function cancelForm() {
  showForm.value = false
  resetForm()
}

function editChannel(ch) {
  editing.value = ch.id
  form.value = {
    name: ch.name,
    type: ch.type,
    config: { ...ch.config },
  }
  showForm.value = true
}

async function saveChannel() {
  formError.value = ''
  try {
    if (editing.value) {
      await api.updateChannel(editing.value, {
        name: form.value.name,
        config: form.value.config,
      })
    } else {
      await api.createChannel(form.value)
    }
    showForm.value = false
    resetForm()
    await loadChannels()
  } catch (e) {
    formError.value = '保存失败: ' + e.message
  }
}

async function removeChannel(ch) {
  if (!confirm(`确认删除通道 "${ch.name}"？`)) return
  await api.deleteChannel(ch.id)
  await loadChannels()
}

async function toggleEnabled(ch) {
  await api.updateChannel(ch.id, { enabled: !ch.enabled })
  await loadChannels()
}

async function testPush(ch) {
  ch.testing = true
  ch.testResult = null
  try {
    ch.testResult = await api.testChannel(ch.id)
  } catch (e) {
    ch.testResult = { ok: false, error: e.message }
  } finally {
    ch.testing = false
  }
}

onMounted(loadChannels)
</script>

<style scoped>
.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.form-group {
  margin-bottom: 16px;
}

.form-group label {
  display: block;
  margin-bottom: 6px;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 600;
}

.form-actions {
  display: flex;
  gap: 12px;
  margin-top: 8px;
}

.form-error {
  color: var(--danger);
  font-size: 13px;
  margin-top: 12px;
}

.channel-card {
  margin-bottom: 12px;
}

.channel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.channel-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.channel-name {
  font-weight: 600;
  font-size: 15px;
}

.channel-type {
  color: var(--text-tertiary);
  font-size: 13px;
}

.channel-actions {
  display: flex;
  gap: 8px;
}

.test-result {
  margin-top: 12px;
  padding: 8px 14px;
  border-radius: var(--radius-sm);
  font-size: 13px;
}

.test-ok {
  background: var(--success-subtle);
  color: var(--success);
}

.test-fail {
  background: var(--danger-subtle);
  color: var(--danger);
}
</style>

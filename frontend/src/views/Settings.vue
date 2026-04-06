<template>
  <div>
    <div class="page-header">
      <h1>系统设置</h1>
      <p>全局配置与服务状态</p>
    </div>

    <!-- System Info -->
    <div class="card section">
      <h3 class="section-title">系统信息</h3>
      <div class="info-grid">
        <div class="info-item">
          <span class="info-label">版本</span>
          <span class="info-value">{{ info.version || '-' }}</span>
        </div>
        <div class="info-item">
          <span class="info-label">缓存 TTL</span>
          <span class="info-value">{{ info.cache_ttl }}s</span>
        </div>
        <div class="info-item">
          <span class="info-label">最小成交额</span>
          <span class="info-value">{{ formatVolume(info.min_volume_24h) }} USDT</span>
        </div>
        <div class="info-item">
          <span class="info-label">代理</span>
          <span class="info-value">{{ info.proxy_enabled ? info.proxy_host + ':' + info.proxy_port : '未启用' }}</span>
        </div>
      </div>
    </div>

    <!-- TradingView Cookies (Pine Screener) -->
    <div class="card section">
      <div class="section-header">
        <h3 class="section-title">TradingView Cookies</h3>
        <span class="badge" :class="pineHasCookies ? 'badge-success' : 'badge-danger'">
          {{ pineHasCookies ? '已配置' : '未配置' }}
        </span>
      </div>
      <p class="section-desc">Pine 指标筛选需要 TradingView 登录态。从浏览器复制 Cookie 字符串粘贴到下方。</p>
      <div class="form-group">
        <textarea
          v-model="pineCookieInput"
          rows="3"
          placeholder="sessionid=xxx; sessionid_sign=xxx; ..."
        ></textarea>
      </div>
      <div class="btn-row">
        <button class="btn btn-primary btn-sm" @click="savePineCookies" :disabled="!pineCookieInput.trim()">
          保存
        </button>
      </div>
      <div v-if="pineCookieMsg" class="action-msg" :class="pineCookieOk ? 'msg-ok' : 'msg-fail'">
        {{ pineCookieMsg }}
      </div>
    </div>

    <!-- ChartShot Service -->
    <div class="card section">
      <div class="section-header">
        <h3 class="section-title">ChartShot 截图服务</h3>
        <span class="badge" :class="chartshotOnline ? 'badge-success' : 'badge-danger'">
          {{ chartshotOnline ? '在线' : '离线' }}
        </span>
      </div>

      <!-- ChartShot Cookies -->
      <p class="section-desc">截图服务需要独立的 TradingView Cookies（与 Pine 筛选共用同一账号即可）。</p>
      <div class="form-group">
        <textarea
          v-model="chartshotCookieInput"
          rows="3"
          placeholder="sessionid=xxx; sessionid_sign=xxx; ..."
        ></textarea>
      </div>
      <div class="btn-row">
        <button class="btn btn-primary btn-sm" @click="saveChartshotCookies" :disabled="!chartshotCookieInput.trim()">
          保存
        </button>
        <button class="btn btn-sm" @click="testChartshotCookies">
          测试连接
        </button>
      </div>
      <div v-if="chartshotMsg" class="action-msg" :class="chartshotOk ? 'msg-ok' : 'msg-fail'">
        {{ chartshotMsg }}
      </div>
    </div>

    <!-- AI Configuration -->
    <div class="card section">
      <div class="section-header">
        <h3 class="section-title">AI 配置</h3>
        <span class="badge" :class="aiHasKey ? 'badge-success' : 'badge-danger'">
          {{ aiHasKey ? '已配置' : '未配置' }}
        </span>
      </div>
      <p class="section-desc">接入 OpenAI 兼容 API，用于信号分析和技术解读。</p>
      <div class="form-row">
        <div class="form-group">
          <label>API Key</label>
          <input type="password" v-model="aiKeyInput" placeholder="sk-..." />
        </div>
        <div class="form-group">
          <label>Base URL</label>
          <input v-model="aiBaseUrl" placeholder="https://api.openai.com/v1" />
        </div>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label>模型</label>
          <input v-model="aiModel" placeholder="gpt-4o" />
        </div>
        <div class="form-group">
          <label>Max Tokens</label>
          <input type="number" v-model.number="aiMaxTokens" />
        </div>
      </div>
      <div class="btn-row">
        <button class="btn btn-primary btn-sm" @click="saveAIConfig">保存</button>
        <button class="btn btn-sm" @click="testAI">测试连接</button>
      </div>
      <div v-if="aiMsg" class="action-msg" :class="aiOk ? 'msg-ok' : 'msg-fail'">{{ aiMsg }}</div>
    </div>

    <!-- Strategy Management -->
    <div class="card section">
      <div class="section-header">
        <h3 class="section-title">分析策略</h3>
        <button class="btn btn-sm" @click="showNewStrategy = !showNewStrategy">新建</button>
      </div>
      <p class="section-desc">管理 AI 分析的 System Prompt，不同策略影响分析角度和风格。</p>

      <div v-if="showNewStrategy" class="strategy-form">
        <div class="form-group">
          <label>策略名称</label>
          <input v-model="newStrategyName" placeholder="例如：激进短线分析" />
        </div>
        <div class="form-group">
          <label>System Prompt</label>
          <textarea v-model="newStrategyPrompt" rows="6" placeholder="输入系统提示词..."></textarea>
        </div>
        <div class="btn-row">
          <button class="btn btn-primary btn-sm" @click="createStrategy">创建</button>
          <button class="btn btn-sm" @click="showNewStrategy = false">取消</button>
        </div>
      </div>

      <div v-for="s in strategies" :key="s.id" class="strategy-item">
        <div class="strategy-header">
          <span class="strategy-name">{{ s.name }}</span>
          <span v-if="s.is_default" class="badge badge-success">默认</span>
          <div class="strategy-actions">
            <button v-if="!s.is_default" class="btn btn-sm" @click="setDefault(s)">设为默认</button>
            <button v-if="!s.is_default" class="btn btn-sm" style="color:var(--danger)" @click="deleteStrat(s)">删除</button>
          </div>
        </div>
        <div v-if="editingStrategy === s.id">
          <textarea v-model="editPrompt" rows="6" style="margin-top: 8px"></textarea>
          <div class="btn-row" style="margin-top: 8px">
            <button class="btn btn-primary btn-sm" @click="saveStrategyEdit(s)">保存</button>
            <button class="btn btn-sm" @click="editingStrategy = null">取消</button>
          </div>
        </div>
        <div v-else class="strategy-preview" @click="startEditStrategy(s)">
          {{ s.system_prompt.substring(0, 120) }}{{ s.system_prompt.length > 120 ? '...' : '' }}
          <span class="edit-hint">点击编辑</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api/client.js'

const info = ref({ version: '', cache_ttl: 0, min_volume_24h: 0, proxy_enabled: false })

const pineHasCookies = ref(false)
const pineCookieInput = ref('')
const pineCookieMsg = ref('')
const pineCookieOk = ref(false)

const chartshotOnline = ref(false)
const chartshotCookieInput = ref('')
const chartshotMsg = ref('')
const chartshotOk = ref(false)

function formatVolume(v) {
  if (!v) return '0'
  if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M'
  if (v >= 1e3) return (v / 1e3).toFixed(0) + 'K'
  return v.toString()
}

async function loadInfo() {
  try { info.value = await api.getSettings() } catch {}
}

async function loadPineCookies() {
  try {
    const res = await api.getCookies()
    pineHasCookies.value = res.has_cookies || false
  } catch {}
}

async function savePineCookies() {
  pineCookieMsg.value = ''
  try {
    const res = await api.updateCookies(pineCookieInput.value)
    if (res.ok) {
      pineCookieMsg.value = `保存成功，共 ${res.count} 个 cookie`
      pineCookieOk.value = true
      pineCookieInput.value = ''
      await loadPineCookies()
    } else {
      pineCookieMsg.value = res.error || '保存失败'
      pineCookieOk.value = false
    }
  } catch (e) {
    pineCookieMsg.value = '保存失败: ' + e.message
    pineCookieOk.value = false
  }
}

async function loadChartshotStatus() {
  try {
    const res = await api.getChartshotStatus()
    chartshotOnline.value = res.ok && res.status === 'ok'
  } catch {
    chartshotOnline.value = false
  }
}

async function saveChartshotCookies() {
  chartshotMsg.value = ''
  try {
    const res = await api.updateChartshotCookies(chartshotCookieInput.value)
    if (res.ok) {
      chartshotMsg.value = '保存成功'
      chartshotOk.value = true
      chartshotCookieInput.value = ''
    } else {
      chartshotMsg.value = res.error || '保存失败'
      chartshotOk.value = false
    }
  } catch (e) {
    chartshotMsg.value = '保存失败: ' + e.message
    chartshotOk.value = false
  }
}

async function testChartshotCookies() {
  chartshotMsg.value = '测试中...'
  chartshotOk.value = false
  try {
    const res = await api.testChartshotCookies()
    if (res.valid) {
      chartshotMsg.value = `连接成功，用户: ${res.username || 'unknown'}`
      chartshotOk.value = true
    } else {
      chartshotMsg.value = '连接失败: ' + (res.error || '未登录')
      chartshotOk.value = false
    }
  } catch (e) {
    chartshotMsg.value = '测试失败: ' + e.message
    chartshotOk.value = false
  }
}

// AI Config
const aiHasKey = ref(false)
const aiKeyInput = ref('')
const aiBaseUrl = ref('https://api.openai.com/v1')
const aiModel = ref('gpt-4o')
const aiMaxTokens = ref(1000)
const aiMsg = ref('')
const aiOk = ref(false)

// Strategies
const strategies = ref([])
const showNewStrategy = ref(false)
const newStrategyName = ref('')
const newStrategyPrompt = ref('')
const editingStrategy = ref(null)
const editPrompt = ref('')

async function loadAIConfig() {
  try {
    const conf = await api.getAIConfig()
    aiHasKey.value = conf.has_key || false
    aiBaseUrl.value = conf.base_url || 'https://api.openai.com/v1'
    aiModel.value = conf.model || 'gpt-4o'
    aiMaxTokens.value = conf.max_tokens || 1000
  } catch {}
}

async function saveAIConfig() {
  aiMsg.value = ''
  const data = { base_url: aiBaseUrl.value, model: aiModel.value, max_tokens: aiMaxTokens.value }
  if (aiKeyInput.value) data.api_key = aiKeyInput.value
  try {
    await api.updateAIConfig(data)
    aiMsg.value = '保存成功'
    aiOk.value = true
    aiKeyInput.value = ''
    await loadAIConfig()
  } catch (e) {
    aiMsg.value = '保存失败: ' + e.message
    aiOk.value = false
  }
}

async function testAI() {
  aiMsg.value = '测试中...'
  try {
    const res = await api.testAIConnection()
    aiMsg.value = res.ok ? `连接成功，${res.models_count} 个模型可用` : ('连接失败: ' + res.error)
    aiOk.value = res.ok
  } catch (e) {
    aiMsg.value = '测试失败: ' + e.message
    aiOk.value = false
  }
}

async function loadStrategies() {
  try { strategies.value = await api.listStrategies() } catch {}
}

async function createStrategy() {
  await api.createStrategy({ name: newStrategyName.value, system_prompt: newStrategyPrompt.value })
  showNewStrategy.value = false
  newStrategyName.value = ''
  newStrategyPrompt.value = ''
  await loadStrategies()
}

function startEditStrategy(s) {
  editingStrategy.value = s.id
  editPrompt.value = s.system_prompt
}

async function saveStrategyEdit(s) {
  await api.updateStrategy(s.id, { system_prompt: editPrompt.value })
  editingStrategy.value = null
  await loadStrategies()
}

async function setDefault(s) {
  await api.setDefaultStrategy(s.id)
  await loadStrategies()
}

async function deleteStrat(s) {
  if (!confirm(`确认删除策略 "${s.name}"？`)) return
  await api.deleteStrategy(s.id)
  await loadStrategies()
}

onMounted(() => {
  loadInfo()
  loadPineCookies()
  loadChartshotStatus()
  loadAIConfig()
  loadStrategies()
})
</script>

<style scoped>
.section {
  margin-bottom: 20px;
}

.section-title {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 12px;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.section-header .section-title {
  margin-bottom: 0;
}

.section-desc {
  color: var(--text-secondary);
  font-size: 13px;
  margin-bottom: 16px;
  line-height: 1.5;
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 16px;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.info-label {
  font-size: 12px;
  color: var(--text-tertiary);
  font-weight: 600;
  letter-spacing: 0.03em;
}

.info-value {
  font-size: 15px;
  font-weight: 500;
}

.form-group {
  margin-bottom: 12px;
}

.form-group textarea {
  font-family: 'Courier New', monospace;
  font-size: 12px;
  resize: vertical;
  min-height: 60px;
}

.btn-row {
  display: flex;
  gap: 8px;
}

.action-msg {
  margin-top: 12px;
  padding: 8px 14px;
  border-radius: var(--radius-sm);
  font-size: 13px;
}

.msg-ok {
  background: var(--success-subtle);
  color: var(--success);
}

.msg-fail {
  background: var(--danger-subtle);
  color: var(--danger);
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.form-group label {
  display: block;
  margin-bottom: 6px;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 600;
}

.strategy-item {
  padding: 12px 0;
  border-bottom: 1px solid var(--border);
}

.strategy-header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.strategy-name {
  font-weight: 600;
  font-size: 14px;
}

.strategy-actions {
  margin-left: auto;
  display: flex;
  gap: 6px;
}

.strategy-preview {
  margin-top: 6px;
  font-size: 13px;
  color: var(--text-secondary);
  cursor: pointer;
  line-height: 1.5;
}

.strategy-preview:hover {
  color: var(--text-primary);
}

.edit-hint {
  color: var(--accent);
  font-size: 12px;
  margin-left: 8px;
}

.strategy-form {
  padding: 16px;
  background: var(--bg-primary);
  border-radius: var(--radius-md);
  margin-bottom: 16px;
}
</style>

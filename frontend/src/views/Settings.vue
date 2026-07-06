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

    <!-- Proxy Configuration -->
    <div class="card section">
      <div class="section-header">
        <h3 class="section-title">网络代理</h3>
        <span class="badge" :class="proxyEnabled ? 'badge-success' : 'badge-danger'">
          {{ proxyEnabled ? '已启用' : '未启用' }}
        </span>
      </div>
      <p class="section-desc">
        HTTP 代理，用于访问被封锁的外部服务。生效范围：交易所 API（Binance/Bybit）、TradingView Pine 筛选 API、AI API。<br>
        <strong>Docker 部署时</strong>，代理地址填 <code>host.docker.internal</code>（宿主机），不要填 127.0.0.1。
      </p>
      <div class="form-row">
        <div class="form-group">
          <label>代理地址</label>
          <input v-model="proxyHost" placeholder="host.docker.internal" />
        </div>
        <div class="form-group">
          <label>端口</label>
          <input v-model="proxyPort" placeholder="24000" />
        </div>
      </div>
      <div class="btn-row">
        <button class="btn btn-primary btn-sm" @click="saveProxy(true)">启用并保存</button>
        <button v-if="proxyEnabled" class="btn btn-sm" @click="saveProxy(false)">停用代理</button>
      </div>
      <div v-if="proxyMsg" class="action-msg" :class="proxyOk ? 'msg-ok' : 'msg-fail'">{{ proxyMsg }}</div>
    </div>

    <!-- Trading Credentials (Binance API keys) -->
    <div class="card section">
      <div class="section-header">
        <h3 class="section-title">交易凭据</h3>
        <span class="badge" :class="tradingCreds.length ? 'badge-success' : 'badge-danger'">
          {{ tradingCreds.length ? `已配置 ${tradingCreds.length} 个` : '未配置' }}
        </span>
      </div>
      <p class="section-desc">
        Binance 永续合约的 API key/secret。<strong>建议先用测试网</strong>（testnet.binancefuture.com）验证后再接入实盘。
        Secret 经 Fernet 加密后存储（密钥派生自 <code>SECRET_KEY</code>）。
        <strong>仅勾选「合约交易」权限，禁用「提现」</strong>，并在 Binance 端绑定 VPS 出口 IP。
      </p>

      <table v-if="tradingCreds.length" class="creds-table">
        <thead>
          <tr>
            <th>标签</th>
            <th>环境</th>
            <th>API Key</th>
            <th>状态</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in tradingCreds" :key="c.id">
            <td>{{ c.label }}</td>
            <td>
              <span class="env-tag" :class="'env-' + c.env">
                {{ c.env === 'mainnet' ? '⚠️ 实盘' : '🧪 测试网' }}
              </span>
            </td>
            <td><code>...{{ c.api_key.slice(-8) }}</code></td>
            <td>
              <span class="badge" :class="c.enabled ? 'badge-success' : 'badge-warning'">
                {{ c.enabled ? '启用' : '禁用' }}
              </span>
            </td>
            <td class="cred-actions">
              <button class="btn btn-sm" @click="testCred(c.id)">测试</button>
              <button class="btn btn-sm" @click="toggleCred(c)">{{ c.enabled ? '禁用' : '启用' }}</button>
              <button class="btn btn-sm btn-danger" @click="deleteCred(c.id)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>

      <div class="cred-form">
        <h4>添加新凭据</h4>
        <div class="form-row">
          <div class="form-group">
            <label>标签（自取）</label>
            <input v-model="newCred.label" placeholder="testnet-主账户" />
          </div>
          <div class="form-group">
            <label>环境</label>
            <select v-model="newCred.env">
              <option value="testnet">测试网 (testnet)</option>
              <option value="mainnet">实盘 (mainnet)</option>
            </select>
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>API Key</label>
            <input v-model="newCred.api_key" placeholder="64 位字母数字" autocomplete="off" />
          </div>
          <div class="form-group">
            <label>API Secret</label>
            <input v-model="newCred.api_secret" placeholder="64 位字母数字" autocomplete="off" type="password" />
          </div>
        </div>
        <div class="btn-row">
          <button class="btn btn-primary btn-sm" @click="addCred" :disabled="!canAddCred">添加</button>
        </div>
        <div v-if="credMsg" class="action-msg" :class="credOk ? 'msg-ok' : 'msg-fail'">{{ credMsg }}</div>
      </div>
    </div>

    <!-- Agent Configuration -->
    <div class="card section">
      <div class="section-header">
        <h3 class="section-title">Agent 配置</h3>
        <span class="badge" :class="agentForm.enabled ? 'badge-success' : 'badge-danger'">
          {{ agentForm.enabled ? '已启用' : '未启用' }}
        </span>
      </div>
      <p class="section-desc">
        配置 AI Agent，用于自动分析信号并生成交易裁决。API Key 经 Fernet 加密后存储（密钥派生自 <code>SECRET_KEY</code>）。
      </p>

      <!-- 安全警告条 -->
      <div v-if="agentInsecureDefaults.length" class="insecure-warning">
        <strong>⚠ 安全警告：</strong>
        {{ agentInsecureDefaults.join('、') }} 为默认值——密钥加密形同虚设；且轮换 SECRET_KEY 会作废已存密钥。
      </div>

      <div class="form-row">
        <div class="form-group">
          <label>启用 Agent</label>
          <label class="toggle-label">
            <input type="checkbox" v-model="agentForm.enabled" class="toggle-cb" />
            <span class="toggle-track"><span class="toggle-thumb"></span></span>
            <span>{{ agentForm.enabled ? '启用' : '禁用' }}</span>
          </label>
        </div>
        <div class="form-group">
          <label>Provider</label>
          <select v-model="agentForm.provider">
            <option value="openai">OpenAI 兼容端点</option>
            <option value="anthropic">Anthropic</option>
          </select>
        </div>
      </div>

      <div v-if="agentForm.provider === 'openai'" class="form-group">
        <label>Base URL</label>
        <input
          v-model="agentForm.base_url"
          placeholder="https://.../v1，留空使用官方端点"
        />
      </div>

      <div class="form-row">
        <div class="form-group">
          <label>模型 <button type="button" class="btn-inline" @click="loadModels">刷新列表</button></label>
          <input v-model="agentForm.model" list="agent-model-list"
                 placeholder="例：deepseek/deepseek-v4-pro（可手输或从列表选）" />
          <datalist id="agent-model-list">
            <option v-for="m in modelList" :key="m" :value="m" />
          </datalist>
        </div>
        <div class="form-group">
          <label>视觉模型（可选，识图/截图分析用）</label>
          <input v-model="agentForm.vision_model" list="agent-model-list"
                 placeholder="留空 = 图片直传主模型（主模型须多模态）" />
        </div>
        <div class="form-group">
          <label>API Key</label>
          <div class="api-key-row">
            <input
              v-model="agentApiKeyInput"
              type="password"
              autocomplete="new-password"
              :placeholder="agentHasApiKey ? '已保存（留空不修改）' : '请输入 API Key'"
              class="api-key-input"
            />
            <button
              v-if="agentHasApiKey"
              class="btn btn-sm btn-danger-outline"
              @click="clearAgentApiKey"
              title="清除已存密钥"
            >清除</button>
          </div>
          <div v-if="agentApiKeyClearedFlag" class="key-clear-hint">提交后将清除已存密钥</div>
        </div>
      </div>

      <div class="form-row">
        <div class="form-group">
          <label>max_tokens（256 – 64000）</label>
          <input v-model.number="agentForm.max_tokens" type="number" min="256" max="64000" />
        </div>
        <div class="form-group">
          <label>max_tool_calls（1 – 50）</label>
          <input v-model.number="agentForm.max_tool_calls" type="number" min="1" max="50" />
        </div>
      </div>

      <div class="form-group">
        <label>deep_dive_limit（0 – 20）</label>
        <input v-model.number="agentForm.deep_dive_limit" type="number" min="0" max="20" />
      </div>

      <div class="form-group">
        <label>绑定交易凭据（可选，用于只读仓位规划预览）</label>
        <select v-model="agentForm.credential_id">
          <option :value="null">不使用</option>
          <option v-for="c in tradingCreds" :key="c.id" :value="c.id">
            {{ c.label }}（{{ c.env === 'mainnet' ? '实盘' : '测试网' }}）
          </option>
        </select>
      </div>

      <div class="btn-row">
        <button class="btn btn-primary btn-sm" @click="saveAgentConfig">保存</button>
        <button type="button" class="btn btn-sm" :disabled="testing" @click="testLlm">
          {{ testing ? '测试中…' : '测试连接' }}
        </button>
        <span v-if="testResult" class="test-result">
          主模型 {{ testResult.main.ok ? '✅' : '❌ ' + testResult.main.error }}
          <template v-if="testResult.vision">
            ｜视觉 {{ testResult.vision.ok ? '✅ 支持图像' : '❌ ' + testResult.vision.error }}
          </template>
        </span>
      </div>
      <div v-if="agentMsg" class="action-msg" :class="agentOk ? 'msg-ok' : 'msg-fail'">
        {{ agentMsg }}
      </div>
    </div>

    <!-- Screener Semantics -->
    <div class="card section">
      <div class="section-header">
        <h3 class="section-title">筛选器语义档案</h3>
      </div>
      <p class="section-desc">
        注入 agent system prompt，让它理解每个筛选器的含义（初稿可直接修改）。
      </p>
      <div v-for="s in semantics" :key="s.key" class="sem-card">
        <div class="sem-head" @click="s._open = !s._open">
          <strong>{{ s.label }}</strong> <code>{{ s.key }}</code>
          <span class="sem-bias">{{ s.bias }}</span>
        </div>
        <div v-if="s._open" class="sem-body">
          <label>含义<textarea v-model="s.meaning" rows="2" /></label>
          <label>方向倾向<input v-model="s.bias" /></label>
          <label>用法<textarea v-model="s.usage" rows="2" /></label>
          <label>局限<textarea v-model="s.caveats" rows="2" /></label>
          <label>建议叠加<textarea v-model="s.combos" rows="2" /></label>
          <button class="btn btn-primary btn-sm" @click="saveSemantics(s)">保存</button>
          <span v-if="s._msg" class="action-msg msg-ok">{{ s._msg }}</span>
        </div>
      </div>
    </div>

    <!-- System Logs -->
    <div class="card section">
      <div class="section-header">
        <h3 class="section-title">系统日志</h3>
        <div class="btn-row">
          <select v-model="logSource" class="log-filter" @change="loadLogs">
            <option value="">全部来源</option>
            <option value="pine_screener">Pine筛选</option>
            <option value="exchange">交易所</option>
            <option value="executor">执行器</option>
          </select>
          <select v-model="logLevel" class="log-filter" @change="loadLogs">
            <option value="">全部级别</option>
            <option value="error">错误</option>
            <option value="warn">警告</option>
            <option value="info">信息</option>
            <option value="debug">调试</option>
          </select>
          <button class="btn btn-sm" @click="loadLogs">刷新</button>
          <button class="btn btn-sm" @click="clearAllLogs">清除</button>
        </div>
      </div>
      <div class="log-panel">
        <div v-if="!logs.length" class="history-empty">暂无日志</div>
        <div v-for="(entry, i) in logs" :key="i" class="log-entry" :class="'log-' + entry.level">
          <span class="log-ts">{{ entry.ts.substring(11) }}</span>
          <span class="log-source">{{ entry.source }}</span>
          <span class="log-level">{{ entry.level }}</span>
          <span class="log-msg">{{ entry.message }}</span>
          <div v-if="entry.detail" class="log-detail">{{ entry.detail }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
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

const proxyEnabled = ref(false)
const proxyHost = ref('host.docker.internal')
const proxyPort = ref('10809')
const proxyMsg = ref('')
const proxyOk = ref(false)

// ---- Trading credentials ----
const tradingCreds = ref([])
const newCred = ref({ label: '', env: 'testnet', api_key: '', api_secret: '' })
const credMsg = ref('')
const credOk = ref(false)
const canAddCred = computed(() => {
  const c = newCred.value
  return c.label.trim() && c.api_key.length >= 10 && c.api_secret.length >= 10
})

async function loadTradingCreds() {
  try {
    const r = await api.listTradingCredentials()
    tradingCreds.value = r.credentials || []
  } catch {
    tradingCreds.value = []
  }
}

async function addCred() {
  credMsg.value = ''
  try {
    await api.addTradingCredential({
      label: newCred.value.label.trim(),
      env: newCred.value.env,
      api_key: newCred.value.api_key.trim(),
      api_secret: newCred.value.api_secret.trim(),
    })
    credMsg.value = '已添加'
    credOk.value = true
    newCred.value = { label: '', env: 'testnet', api_key: '', api_secret: '' }
    await loadTradingCreds()
  } catch (e) {
    credMsg.value = e.message || '添加失败'
    credOk.value = false
  }
}

async function deleteCred(id) {
  if (!confirm('确认删除这个凭据？历史订单记录会保留。')) return
  try {
    await api.deleteTradingCredential(id)
    await loadTradingCreds()
  } catch (e) {
    credMsg.value = e.message; credOk.value = false
  }
}

async function toggleCred(c) {
  try {
    await api.toggleTradingCredential(c.id, !c.enabled)
    await loadTradingCreds()
  } catch (e) {
    credMsg.value = e.message; credOk.value = false
  }
}

async function testCred(id) {
  credMsg.value = '测试中…'
  credOk.value = true
  try {
    const r = await api.testTradingCredential(id)
    credMsg.value = `✓ 凭据可用（${r.env} · ...${r.api_key_tail}）`
    credOk.value = true
  } catch (e) {
    credMsg.value = `✗ ${e.message}`
    credOk.value = false
  }
}

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

async function loadProxy() {
  try {
    const res = await api.getProxy()
    proxyEnabled.value = res.enabled || false
    proxyHost.value = res.host || '127.0.0.1'
    proxyPort.value = res.port || '24000'
  } catch {}
}

async function saveProxy(enabled) {
  proxyMsg.value = ''
  try {
    const res = await api.updateProxy({ enabled, host: proxyHost.value, port: proxyPort.value })
    if (res.ok) {
      proxyMsg.value = enabled ? `代理已启用: ${res.host}:${res.port}` : '代理已停用'
      proxyOk.value = true
      proxyEnabled.value = enabled
      await loadInfo()
    } else {
      proxyMsg.value = '保存失败'
      proxyOk.value = false
    }
  } catch (e) {
    proxyMsg.value = '保存失败: ' + e.message
    proxyOk.value = false
  }
}

// ---- Agent configuration ----
const agentForm = ref({
  enabled: false,
  provider: 'openai',
  base_url: '',
  model: '',
  vision_model: '',
  max_tokens: 4096,
  max_tool_calls: 15,
  deep_dive_limit: 5,
  credential_id: null,
})
const agentHasApiKey = ref(false)
const agentApiKeyInput = ref('')
const agentApiKeyClearedFlag = ref(false)
const agentInsecureDefaults = ref([])
const agentMsg = ref('')
const agentOk = ref(false)
const modelList = ref([])
const testing = ref(false)
const testResult = ref(null)

async function loadAgentConfig() {
  try {
    const r = await api.getAgentConfig()
    agentHasApiKey.value = r.has_api_key || false
    agentInsecureDefaults.value = r.insecure_defaults || []
    agentForm.value = {
      enabled: r.enabled ?? false,
      provider: r.provider || 'openai',
      base_url: r.base_url || '',
      model: r.model || '',
      vision_model: r.vision_model || '',
      max_tokens: r.max_tokens ?? 4096,
      max_tool_calls: r.max_tool_calls ?? 15,
      deep_dive_limit: r.deep_dive_limit ?? 5,
      credential_id: r.credential_id ?? null,
    }
  } catch {}
}

function clearAgentApiKey() {
  agentApiKeyInput.value = ''
  agentApiKeyClearedFlag.value = true
}

async function saveAgentConfig() {
  agentMsg.value = ''
  try {
    // api_key: null = 不修改；"" = 显式清除；字符串 = 更新
    let api_key = null
    if (agentApiKeyClearedFlag.value) {
      api_key = ''
    } else if (agentApiKeyInput.value.trim()) {
      api_key = agentApiKeyInput.value.trim()
    }
    const payload = { ...agentForm.value, api_key }
    const r = await api.updateAgentConfig(payload)
    agentHasApiKey.value = r.has_api_key || false
    agentInsecureDefaults.value = r.insecure_defaults || []
    agentMsg.value = '保存成功'
    agentOk.value = true
    agentApiKeyInput.value = ''
    agentApiKeyClearedFlag.value = false
  } catch (e) {
    agentMsg.value = e.message || '保存失败'
    agentOk.value = false
  }
}

function agentOverrides() {
  const o = { provider: agentForm.value.provider, base_url: agentForm.value.base_url,
              model: agentForm.value.model, vision_model: agentForm.value.vision_model }
  if (agentApiKeyInput.value.trim()) o.api_key = agentApiKeyInput.value.trim()
  return o
}

async function loadModels() {
  try { modelList.value = (await api.fetchAgentModels(agentOverrides())).models }
  catch (e) { alert('模型列表获取失败：' + e.message) }
}

async function testLlm() {
  testing.value = true
  testResult.value = null
  try { testResult.value = await api.testAgentLlm(agentOverrides()) }
  catch (e) { testResult.value = { main: { ok: false, error: e.message }, vision: null } }
  finally { testing.value = false }
}

// ---- Screener semantics ----
const semantics = ref([])

async function loadSemantics() {
  semantics.value = (await api.getScreenerSemantics()).map(s => ({ ...s, _open: false, _msg: '' }))
}

async function saveSemantics(s) {
  await api.saveScreenerSemantics(s.key, {
    meaning: s.meaning, bias: s.bias, usage: s.usage, caveats: s.caveats, combos: s.combos,
  })
  s._msg = '已保存'
  setTimeout(() => { s._msg = '' }, 2000)
}

// Logs
const logs = ref([])
const logSource = ref('')
const logLevel = ref('')

async function loadLogs() {
  try { logs.value = await api.getLogs(logSource.value || null, logLevel.value || null) } catch {}
}

async function clearAllLogs() {
  await api.clearLogs()
  logs.value = []
}

onMounted(() => {
  loadInfo()
  loadPineCookies()
  loadChartshotStatus()
  loadProxy()
  loadTradingCreds()
  loadAgentConfig()
  loadSemantics()
  loadLogs()
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

.log-filter {
  max-width: 130px;
  padding: 5px 8px;
  font-size: 12px;
}

.log-panel {
  max-height: 400px;
  overflow-y: auto;
  font-family: 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.5;
  margin-top: 12px;
}

.log-entry {
  padding: 4px 8px;
  border-bottom: 1px solid var(--border-subtle, var(--border));
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: baseline;
}

.log-entry:hover {
  background: var(--bg-tertiary);
}

.log-ts {
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.log-source {
  color: var(--accent);
  font-weight: 600;
  min-width: 90px;
}

.log-level {
  font-weight: 600;
  min-width: 40px;
}

.log-error .log-level { color: var(--danger); }
.log-warn .log-level { color: var(--warning); }
.log-info .log-level { color: var(--success); }
.log-debug .log-level { color: var(--text-tertiary); }

.log-msg {
  flex: 1;
  color: var(--text-primary);
}

.log-detail {
  width: 100%;
  padding: 4px 8px 4px 140px;
  color: var(--text-secondary);
  word-break: break-all;
  font-size: 11px;
}

.history-empty {
  text-align: center;
  padding: 20px;
  color: var(--text-tertiary);
  font-size: 13px;
}

/* ---- Trading credentials ---- */
.creds-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  margin-bottom: 20px;
}
.creds-table thead th {
  text-align: left;
  padding: 10px 14px;
  color: var(--text-secondary);
  font-weight: 600;
  font-size: 12px;
  border-bottom: 1px solid var(--border);
}
.creds-table tbody td {
  padding: 12px 14px;
  border-bottom: 1px solid var(--border);
  font-variant-numeric: tabular-nums;
}
.creds-table code {
  background: var(--bg-tertiary);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12px;
}
.cred-actions {
  display: flex;
  gap: 6px;
  justify-content: flex-end;
}
.btn-danger {
  color: var(--danger);
}
.btn-danger:hover {
  background: var(--danger-subtle);
}
.env-tag {
  font-size: 11px;
  font-weight: 500;
  padding: 3px 10px;
  border-radius: 20px;
}
.env-tag.env-testnet {
  background: var(--accent-subtle);
  color: var(--accent);
}
.env-tag.env-mainnet {
  background: var(--danger-subtle);
  color: var(--danger);
}
.cred-form {
  padding-top: 12px;
  border-top: 1px solid var(--border);
}
.cred-form h4 {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 14px;
  color: var(--text-primary);
}

/* ---- Agent configuration ---- */
.insecure-warning {
  margin-bottom: 16px;
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  background: var(--warning-subtle, var(--danger-subtle));
  color: var(--warning, var(--danger));
  font-size: 13px;
  line-height: 1.5;
}

/* Toggle switch */
.toggle-label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  user-select: none;
  font-size: 13px;
  color: var(--text-primary);
}

.toggle-cb {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}

.toggle-track {
  position: relative;
  display: inline-block;
  width: 36px;
  height: 20px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  border-radius: 20px;
  transition: background 0.2s, border-color 0.2s;
  flex-shrink: 0;
}

.toggle-cb:checked + .toggle-track {
  background: var(--accent);
  border-color: var(--accent);
}

.toggle-thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 14px;
  height: 14px;
  background: var(--text-secondary);
  border-radius: 50%;
  transition: transform 0.2s, background 0.2s;
}

.toggle-cb:checked + .toggle-track .toggle-thumb {
  transform: translateX(16px);
  background: #fff;
}

/* API key row */
.api-key-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.api-key-input {
  flex: 1;
}

.btn-danger-outline {
  color: var(--danger);
  border-color: var(--danger);
  background: transparent;
  flex-shrink: 0;
}

.btn-danger-outline:hover {
  background: var(--danger-subtle);
}

.key-clear-hint {
  font-size: 12px;
  color: var(--warning, var(--danger));
  margin-top: 4px;
}

.btn-inline { font-size: 12px; padding: 1px 8px; margin-left: 8px; cursor: pointer; }
.test-result { font-size: 12.5px; margin-left: 10px; }

/* ---- Screener semantics ---- */
.sem-card { border: 1px solid rgba(128,128,128,.2); border-radius: 8px; margin-bottom: 8px; }
.sem-head { display: flex; gap: 10px; align-items: center; padding: 8px 12px; cursor: pointer; }
.sem-bias { margin-left: auto; font-size: 12px; opacity: .7; }
.sem-body { padding: 0 12px 12px; display: flex; flex-direction: column; gap: 6px; }
.sem-body label { display: flex; flex-direction: column; font-size: 12.5px; gap: 2px; }
</style>

<template>
  <div>
    <div class="page-header">
      <h1>合约交易</h1>
      <p>币安 USDT 永续 · 一键开仓</p>
    </div>

    <!-- Empty state: no credentials configured -->
    <div v-if="!credentials.length" class="card empty-card">
      <h3>尚未配置 API Key</h3>
      <p>请先到「系统设置 → 交易凭据」添加 Binance API key。建议先用测试网（testnet）验证后再切实盘。</p>
      <router-link class="btn btn-primary" to="/settings">前往设置</router-link>
    </div>

    <template v-else>
      <!-- Credential selector + env banner -->
      <div class="card toolbar-card">
        <div class="ctrl">
          <label>使用凭据</label>
          <select v-model="selectedCredentialId" @change="reloadAccount">
            <option v-for="c in credentials" :key="c.id" :value="c.id" :disabled="!c.enabled">
              {{ c.label }} ({{ c.env }}) · {{ c.api_key.slice(-6) }}{{ c.enabled ? '' : ' [已禁用]' }}
            </option>
          </select>
        </div>
        <div class="env-badge" :class="'env-' + currentEnv">
          {{ currentEnv === 'mainnet' ? '⚠️ 实盘' : '🧪 测试网' }}
        </div>
        <button class="btn btn-compact" @click="reloadAccount" :disabled="loadingAccount">
          {{ loadingAccount ? '加载中…' : '刷新账户' }}
        </button>
      </div>

      <!-- Account snapshot -->
      <div v-if="account" class="account-row">
        <div class="card stat-card">
          <div class="stat-label">钱包余额 (USDT)</div>
          <div class="stat-value">{{ fmt(account.total_wallet_balance) }}</div>
        </div>
        <div class="card stat-card">
          <div class="stat-label">可用保证金</div>
          <div class="stat-value">{{ fmt(account.available_balance) }}</div>
        </div>
        <div class="card stat-card" :class="account.total_unrealized_pnl >= 0 ? 'clr-positive' : 'clr-negative'">
          <div class="stat-label">未实现盈亏</div>
          <div class="stat-value">{{ pnlSign(account.total_unrealized_pnl) }}{{ fmt(account.total_unrealized_pnl) }}</div>
        </div>
        <div class="card stat-card">
          <div class="stat-label">活跃持仓</div>
          <div class="stat-value">{{ account.positions.length }}</div>
        </div>
      </div>

      <!-- Open-position form -->
      <div class="card form-card">
        <h3>开仓</h3>

        <div class="form-grid">
          <div class="form-row">
            <label>币种</label>
            <input v-model.trim="form.symbol" placeholder="BTCUSDT" class="symbol-input" />
          </div>

          <div class="form-row">
            <label>方向</label>
            <div class="radio-group">
              <label class="radio-pill" :class="{ active: form.side === 'BUY' }">
                <input type="radio" value="BUY" v-model="form.side" /> 做多
              </label>
              <label class="radio-pill" :class="{ active: form.side === 'SELL' }">
                <input type="radio" value="SELL" v-model="form.side" /> 做空
              </label>
            </div>
          </div>

          <div class="form-row">
            <label>订单类型</label>
            <div class="radio-group">
              <label class="radio-pill" :class="{ active: form.order_type === 'MARKET' }">
                <input type="radio" value="MARKET" v-model="form.order_type" /> 市价
              </label>
              <label class="radio-pill" :class="{ active: form.order_type === 'LIMIT' }">
                <input type="radio" value="LIMIT" v-model="form.order_type" /> 限价
              </label>
            </div>
          </div>

          <div class="form-row">
            <label>合约数量</label>
            <input v-model.number="form.quantity" type="number" step="0.001" min="0" placeholder="0.001" />
          </div>

          <div v-if="form.order_type === 'LIMIT'" class="form-row">
            <label>限价</label>
            <input v-model.number="form.price" type="number" step="0.01" placeholder="70000.00" />
          </div>

          <div class="form-row">
            <label>杠杆</label>
            <input v-model.number="form.leverage" type="number" min="1" max="125" />
          </div>

          <div class="form-row">
            <label>保证金模式</label>
            <div class="radio-group">
              <label class="radio-pill" :class="{ active: form.margin_type === 'ISOLATED' }">
                <input type="radio" value="ISOLATED" v-model="form.margin_type" /> 逐仓
              </label>
              <label class="radio-pill" :class="{ active: form.margin_type === 'CROSSED' }">
                <input type="radio" value="CROSSED" v-model="form.margin_type" /> 全仓
              </label>
            </div>
          </div>
        </div>

        <div class="form-actions">
          <button
            class="btn btn-primary"
            :disabled="!canSubmit"
            @click="openConfirm"
          >下单（预览）</button>
        </div>

        <p v-if="lastResult" class="result-line" :class="lastResult.ok ? 'clr-positive' : 'clr-negative'">
          {{ lastResult.ok
            ? `✓ 下单成功，订单号 ${lastResult.binance_order_id} · 状态 ${lastResult.status}`
            : `✗ 下单失败：${lastResult.error}` }}
        </p>
      </div>

      <!-- Positions table -->
      <div v-if="account?.positions?.length" class="card positions-card">
        <h3>当前持仓</h3>
        <table>
          <thead>
            <tr>
              <th>币种</th>
              <th>方向</th>
              <th>数量</th>
              <th>开仓均价</th>
              <th>标记价</th>
              <th>未实现盈亏</th>
              <th>杠杆 / 模式</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in account.positions" :key="p.symbol + p.position_amt">
              <td class="col-symbol">{{ p.symbol }}</td>
              <td :class="p.position_amt > 0 ? 'clr-positive' : 'clr-negative'">
                {{ p.position_amt > 0 ? '多' : '空' }}
              </td>
              <td>{{ Math.abs(p.position_amt) }}</td>
              <td>{{ fmt(p.entry_price) }}</td>
              <td>{{ fmt(p.mark_price) }}</td>
              <td :class="p.unrealized_pnl >= 0 ? 'clr-positive' : 'clr-negative'">
                {{ pnlSign(p.unrealized_pnl) }}{{ fmt(p.unrealized_pnl) }}
              </td>
              <td>×{{ p.leverage }} / {{ p.margin_type }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- Confirm modal -->
    <div v-if="confirmOpen" class="modal-mask" @click.self="confirmOpen = false">
      <div class="modal-card">
        <h3>确认下单</h3>
        <div class="confirm-line">
          <span class="confirm-label">币种</span>
          <span class="confirm-value">{{ form.symbol.toUpperCase() }}</span>
        </div>
        <div class="confirm-line">
          <span class="confirm-label">方向 / 类型</span>
          <span class="confirm-value" :class="form.side === 'BUY' ? 'clr-positive' : 'clr-negative'">
            {{ form.side === 'BUY' ? '做多' : '做空' }} · {{ form.order_type === 'MARKET' ? '市价' : '限价' }}
          </span>
        </div>
        <div class="confirm-line">
          <span class="confirm-label">数量</span>
          <span class="confirm-value">{{ form.quantity }} 张</span>
        </div>
        <div v-if="form.order_type === 'LIMIT'" class="confirm-line">
          <span class="confirm-label">限价</span>
          <span class="confirm-value">{{ form.price }}</span>
        </div>
        <div class="confirm-line">
          <span class="confirm-label">杠杆 / 模式</span>
          <span class="confirm-value">×{{ form.leverage }} / {{ form.margin_type === 'ISOLATED' ? '逐仓' : '全仓' }}</span>
        </div>
        <div class="confirm-line">
          <span class="confirm-label">环境</span>
          <span class="confirm-value">
            <span class="env-badge" :class="'env-' + currentEnv">
              {{ currentEnv === 'mainnet' ? '⚠️ 实盘' : '🧪 测试网' }}
            </span>
          </span>
        </div>
        <p v-if="submitError" class="modal-error">{{ submitError }}</p>
        <div class="modal-actions">
          <button class="btn" @click="confirmOpen = false" :disabled="submitting">取消</button>
          <button
            class="btn btn-primary"
            @click="submitOrder"
            :disabled="submitting"
          >{{ submitting ? '提交中…' : '确认提交' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api/client.js'

const credentials = ref([])
const selectedCredentialId = ref(null)
const account = ref(null)
const loadingAccount = ref(false)

const form = ref({
  symbol: 'BTCUSDT',
  side: 'BUY',
  order_type: 'MARKET',
  quantity: 0.001,
  price: null,
  leverage: 10,
  margin_type: 'ISOLATED',
})

const confirmOpen = ref(false)
const submitting = ref(false)
const submitError = ref('')
const lastResult = ref(null)

const currentEnv = computed(() => {
  const c = credentials.value.find(c => c.id === selectedCredentialId.value)
  return c?.env || 'testnet'
})

const canSubmit = computed(() => {
  if (!selectedCredentialId.value) return false
  if (!form.value.symbol || !form.value.quantity || form.value.quantity <= 0) return false
  if (form.value.order_type === 'LIMIT' && (!form.value.price || form.value.price <= 0)) return false
  return true
})

async function reloadCredentials() {
  try {
    const { credentials: list } = await api.listTradingCredentials()
    credentials.value = list
    if (!selectedCredentialId.value && list.length) {
      const enabled = list.find(c => c.enabled) || list[0]
      selectedCredentialId.value = enabled.id
    }
  } catch (e) {
    credentials.value = []
  }
}

async function reloadAccount() {
  if (!selectedCredentialId.value) return
  loadingAccount.value = true
  try {
    account.value = await api.getTradingAccount(selectedCredentialId.value)
  } catch (e) {
    account.value = null
    submitError.value = e.message
  } finally {
    loadingAccount.value = false
  }
}

function openConfirm() {
  submitError.value = ''
  lastResult.value = null
  confirmOpen.value = true
}

async function submitOrder() {
  submitError.value = ''
  submitting.value = true
  try {
    const payload = {
      credential_id: selectedCredentialId.value,
      symbol: form.value.symbol.toUpperCase(),
      side: form.value.side,
      order_type: form.value.order_type,
      quantity: form.value.quantity,
      leverage: form.value.leverage,
      margin_type: form.value.margin_type,
      reduce_only: false,
    }
    if (form.value.order_type === 'LIMIT') payload.price = form.value.price

    const res = await api.placeTradingOrder(payload)
    lastResult.value = res
    if (!res.ok) {
      submitError.value = res.error
      return  // keep modal open so user can see error
    }
    confirmOpen.value = false
    await reloadAccount()
  } catch (e) {
    submitError.value = e.message
  } finally {
    submitting.value = false
  }
}

function fmt(n) {
  if (n == null || isNaN(n)) return '-'
  return Number(n).toLocaleString('en-US', { maximumFractionDigits: 4 })
}

function pnlSign(n) {
  if (n == null || isNaN(n)) return ''
  return n > 0 ? '+' : ''
}

onMounted(async () => {
  await reloadCredentials()
  if (selectedCredentialId.value) {
    await reloadAccount()
  }
})
</script>

<style scoped>
.empty-card {
  text-align: center;
  padding: 48px 32px;
}
.empty-card h3 { margin-bottom: 8px; color: var(--text-primary); }
.empty-card p { margin-bottom: 20px; color: var(--text-secondary); }

.toolbar-card {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 18px 22px;
  margin-bottom: 18px;
}

.ctrl {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
}
.ctrl label {
  font-size: 12px;
  color: var(--text-secondary);
  font-weight: 500;
}
.ctrl select { min-width: 280px; }

.env-badge {
  font-size: 12px;
  font-weight: 600;
  padding: 4px 12px;
  border-radius: 20px;
}
.env-testnet {
  background: var(--accent-subtle);
  color: var(--accent);
}
.env-mainnet {
  background: var(--danger-subtle);
  color: var(--danger);
}

.btn.btn-compact {
  padding: 8px 16px;
  font-size: 13px;
}

.account-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
  margin-bottom: 18px;
}
.stat-card {
  padding: 18px 20px;
}
.stat-label {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 8px;
  letter-spacing: 0.02em;
}
.stat-value {
  font-size: 22px;
  font-weight: 600;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.form-card {
  margin-bottom: 18px;
}
.form-card h3 {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 18px;
  color: var(--text-primary);
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 18px 28px;
  margin-bottom: 20px;
}
.form-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.form-row label {
  font-size: 12px;
  color: var(--text-secondary);
  font-weight: 500;
  letter-spacing: 0.02em;
}

.radio-group {
  display: flex;
  gap: 8px;
}
.radio-pill {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 10px 16px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-md);
  background: var(--bg-primary);
  color: var(--text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all var(--transition-fast);
}
.radio-pill input { display: none; }
.radio-pill.active {
  background: var(--accent-subtle);
  border-color: var(--accent);
  color: var(--accent);
  font-weight: 500;
}

.form-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.result-line {
  margin-top: 14px;
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.result-line.clr-positive { background: var(--success-subtle); }
.result-line.clr-negative { background: var(--danger-subtle); }

.positions-card { padding: 22px 24px; }
.positions-card h3 {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 14px;
  color: var(--text-primary);
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
thead th {
  text-align: left;
  padding: 10px 14px;
  color: var(--text-secondary);
  font-weight: 600;
  font-size: 12px;
  border-bottom: 1px solid var(--border);
}
tbody td {
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  font-variant-numeric: tabular-nums;
}
.col-symbol { font-weight: 600; }

.clr-positive { color: var(--success); }
.clr-negative { color: var(--danger); }

/* ---- modal ---- */
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.modal-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 26px 28px;
  width: 100%;
  max-width: 440px;
  box-shadow: var(--shadow-lg);
}
.modal-card h3 {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 18px;
  color: var(--text-primary);
}
.confirm-line {
  display: flex;
  justify-content: space-between;
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
}
.confirm-line:last-of-type { border-bottom: none; }
.confirm-label {
  font-size: 13px;
  color: var(--text-secondary);
}
.confirm-value {
  font-size: 13px;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
  font-weight: 500;
}
.modal-error {
  background: var(--danger-subtle);
  color: var(--danger);
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  margin: 12px 0;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 18px;
}

@media (max-width: 880px) {
  .account-row { grid-template-columns: repeat(2, 1fr); }
  .form-grid { grid-template-columns: 1fr; }
}
</style>

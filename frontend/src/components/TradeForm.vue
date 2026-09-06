<template>
  <div class="trade-form-wrap">
    <h3>开仓</h3>
    <div class="form-grid">
      <div class="form-row">
        <label>方向</label>
        <div class="radio-group">
          <label class="radio-pill" :class="{ active: form.side === 'BUY', bull: true }">
            <input type="radio" value="BUY" v-model="form.side" /> 做多
          </label>
          <label class="radio-pill" :class="{ active: form.side === 'SELL', bear: true }">
            <input type="radio" value="SELL" v-model="form.side" /> 做空
          </label>
        </div>
      </div>

      <div class="form-row">
        <label>订单类型</label>
        <div class="radio-group">
          <label class="radio-pill active">
            <input type="radio" value="MARKET" v-model="form.order_type" /> 市价 · 严格风控
          </label>
        </div>
        <small class="field-hint">限价单需成交联动保护，当前严格模式暂不开放</small>
      </div>

      <div class="form-row">
        <label>{{ useSL ? '合约数量（自动）' : '合约数量' }}</label>
        <input
          v-model.number="form.quantity"
          type="number" step="0.001" min="0" placeholder="0.001"
          :disabled="useSL"
        />
        <small class="field-hint">
          {{ useSL ? '系统将按总资金 × 风险% ÷ 止损距离反算' : '未填止损时，系统按此数量反算固定亏损位置' }}
        </small>
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

    <div class="risk-policy">
      <div class="risk-title">
        <strong>强制资金风控</strong>
        <span>每笔开仓必须带止损与止盈</span>
      </div>
      <div class="risk-funds">
        <div><label>当前合约总资金</label><b>{{ fmtMoney(accountEquity) }} U</b></div>
        <div><label>本单风险金额</label><b>{{ fmtMoney(riskAmount) }} U</b></div>
      </div>
      <div class="plan-inputs">
        <label>
          风险%
          <input v-model.number="planParams.risk_pct" type="number" step="0.1" min="0.1" max="2" />
        </label>
        <label>
          最低盈亏比
          <input v-model.number="planParams.rr" type="number" step="0.1" min="1.5" />
        </label>
      </div>
      <p class="risk-hint">风险最高 2%，盈亏比最低 1.5；杠杆只影响保证金，不参与开仓量公式。手续费、资金费和滑点不计入该金额。</p>
    </div>

    <!-- smart plan: structure stop + risk-defined sizing -->
    <div class="plan-row">
      <div class="plan-inputs">
        <label>ATR× <input v-model.number="planParams.atr_mult" type="number" step="0.05" min="0" /></label>
      </div>
      <button
        class="btn btn-secondary plan-btn"
        :disabled="!credentialId || !symbol || computing"
        @click="emit('compute-plan', buildPlanRequest())"
      >
        {{ computing ? '计算中…' : '📐 智能计算（结构止损+仓位）' }}
      </button>
    </div>

    <!-- SL / TP -->
    <div class="protection-row">
      <label class="check-row">
        <input type="checkbox" v-model="useSL" />
        <span class="check-label">止损 (SL)</span>
        <input
          v-if="useSL"
          v-model.number="form.stop_loss_price"
          type="number" step="0.01" placeholder="触发价"
          class="protection-input"
        />
      </label>
      <label class="check-row">
        <input type="checkbox" v-model="useTP" />
        <span class="check-label">止盈 (TP)</span>
        <input
          v-if="useTP"
          v-model.number="form.take_profit_price"
          type="number" step="0.01" placeholder="触发价"
          class="protection-input"
        />
      </label>
    </div>
    <p class="protection-hint">
      可手动指定；留空时系统自动反算。SL/TP 触发时按市价
      <strong>整仓平掉</strong>（closePosition=true）。
    </p>

    <div class="form-actions">
      <button
        class="btn btn-primary"
        :disabled="!canSubmit || submitting"
        :class="form.side === 'BUY' ? 'btn-buy' : 'btn-sell'"
        :title="tradingAllowed ? '' : tradingLockReason"
        @click="emit('open-confirm', buildPayload())"
      >
        {{ submitting
          ? '提交中…'
          : (!tradingAllowed
            ? '风险锁定，禁止开仓'
            : (form.side === 'BUY' ? '做多 / 开仓' : '做空 / 开仓')) }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'

const props = defineProps({
  symbol: { type: String, required: true },
  credentialId: { type: Number, default: null },
  accountEquity: { type: Number, default: 0 },
  tradingAllowed: { type: Boolean, default: false },
  tradingLockReason: { type: String, default: '' },
  submitting: { type: Boolean, default: false },
  computing: { type: Boolean, default: false },
})

const emit = defineEmits(['open-confirm', 'compute-plan'])

const planParams = ref({ risk_pct: 1.0, rr: 1.5, atr_mult: 0.3 })

const form = ref({
  side: 'BUY',
  order_type: 'MARKET',
  quantity: 0.001,
  price: null,
  leverage: 10,
  margin_type: 'ISOLATED',
  stop_loss_price: null,
  take_profit_price: null,
})

const useSL = ref(false)
const useTP = ref(false)

// When toggled off, clear the value
watch(useSL, (v) => { if (!v) form.value.stop_loss_price = null })
watch(useTP, (v) => { if (!v) form.value.take_profit_price = null })

const canSubmit = computed(() => {
  if (!props.credentialId || !props.symbol) return false
  if (!props.tradingAllowed) return false
  const f = form.value
  if (!props.accountEquity || props.accountEquity <= 0) return false
  if (!planParams.value.risk_pct || planParams.value.risk_pct <= 0 || planParams.value.risk_pct > 2) return false
  if (!planParams.value.rr || planParams.value.rr < 1.5) return false
  if (!useSL.value && (!f.quantity || f.quantity <= 0)) return false
  if (useSL.value && (!f.stop_loss_price || f.stop_loss_price <= 0)) return false
  if (useTP.value && (!f.take_profit_price || f.take_profit_price <= 0)) return false
  return true
})

const riskAmount = computed(() => {
  const equity = Number(props.accountEquity || 0)
  const pct = Number(planParams.value.risk_pct || 0)
  return equity * pct / 100
})

function normalizedSymbol() {
  const v = props.symbol.trim().toUpperCase()
  return v.endsWith('USDT') ? v : `${v}USDT`
}

function buildPayload() {
  const f = form.value
  return {
    credential_id: props.credentialId,
    symbol: normalizedSymbol(),
    side: f.side,
    order_type: 'MARKET',
    quantity: useSL.value ? null : f.quantity,
    price: null,
    leverage: f.leverage,
    margin_type: f.margin_type,
    stop_loss_price: useSL.value ? f.stop_loss_price : null,
    take_profit_price: useTP.value ? f.take_profit_price : null,
    risk_pct: planParams.value.risk_pct,
    reward_risk_ratio: planParams.value.rr,
  }
}

function buildPlanRequest() {
  const f = form.value
  return {
    direction: f.side === 'BUY' ? 'long' : 'short',
    order_type: 'MARKET',
    entry_price: null,
    leverage: f.leverage,
    risk_pct: planParams.value.risk_pct,
    rr: planParams.value.rr,
    atr_mult: planParams.value.atr_mult,
  }
}

// Called by the parent (Trade.vue) after /trading/plan returns, to fill the form.
function applyPlan(plan) {
  // Never auto-fill the order form from an infeasible plan — the summary panel
  // surfaces the warnings; filling unsafe SL/TP/qty here could be submitted blindly.
  if (!plan || plan.feasible === false) return
  if (plan.quantity && plan.quantity > 0) form.value.quantity = plan.quantity
  if (plan.stop_price && plan.stop_price > 0) {
    useSL.value = true
    form.value.stop_loss_price = plan.stop_price
  }
  if (plan.take_profit_price && plan.take_profit_price > 0) {
    useTP.value = true
    form.value.take_profit_price = plan.take_profit_price
  }
}

// Called by Trade.vue when navigating from the review page with a direction query param.
// Review page uses 'long'/'short'; the form stores 'BUY'/'SELL' internally.
function setDirection(dir) {
  if (dir === 'long') form.value.side = 'BUY'
  else if (dir === 'short') form.value.side = 'SELL'
}

function fmtMoney(n) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return Number(n).toFixed(2)
}

defineExpose({ applyPlan, setDirection })
</script>

<style scoped>
.trade-form-wrap h3 {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 18px;
  color: var(--text-primary);
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px 24px;
  margin-bottom: 18px;
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
.field-hint {
  color: var(--text-tertiary);
  font-size: 11px;
  line-height: 1.4;
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
  padding: 10px 14px;
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
.radio-pill.bull.active {
  background: var(--success-subtle);
  border-color: var(--success);
  color: var(--success);
}
.radio-pill.bear.active {
  background: var(--danger-subtle);
  border-color: var(--danger);
  color: var(--danger);
}

.protection-row {
  display: flex;
  gap: 18px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.check-row {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-primary);
  cursor: pointer;
}
.check-label { font-weight: 500; }
.protection-input {
  width: 130px;
  padding: 6px 10px;
  font-size: 13px;
}
.protection-hint {
  font-size: 12px;
  color: var(--text-tertiary);
  margin: 8px 0 14px;
}

.form-actions {
  display: flex;
  justify-content: stretch;
  margin-top: 6px;
}
.form-actions .btn {
  flex: 1;
  padding: 12px 22px;
  font-weight: 600;
  font-size: 14px;
}
.btn-buy {
  background: var(--success);
  color: white;
}
.btn-buy:hover {
  background: var(--success);
  filter: brightness(1.1);
}
.btn-sell {
  background: var(--danger);
  color: white;
}
.btn-sell:hover {
  background: var(--danger);
  filter: brightness(1.1);
}
.btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
  filter: none;
}

.plan-row {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 14px;
  padding: 12px;
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-md);
  background: var(--bg-primary);
}
.risk-policy {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 14px;
  padding: 14px;
  border: 1px solid var(--accent);
  border-radius: var(--radius-md);
  background: var(--accent-subtle);
}
.risk-title {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
}
.risk-title strong { font-size: 14px; color: var(--accent); }
.risk-title span { font-size: 11px; color: var(--text-secondary); }
.risk-funds {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.risk-funds > div {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.risk-funds label { font-size: 11px; color: var(--text-secondary); }
.risk-funds b { font-size: 16px; font-variant-numeric: tabular-nums; }
.risk-hint {
  margin: 0;
  color: var(--text-secondary);
  font-size: 11.5px;
  line-height: 1.5;
}
.plan-inputs {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.plan-inputs label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-secondary);
}
.plan-inputs input {
  width: 72px;
  padding: 5px 8px;
  font-size: 13px;
}
.plan-btn { width: 100%; font-weight: 600; }
</style>

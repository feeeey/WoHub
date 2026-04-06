<template>
  <div>
    <div class="page-header">
      <h1>信号分析</h1>
      <p>AI 驱动的技术信号解读</p>
    </div>

    <div class="ai-layout">
      <!-- Signal List -->
      <div class="signal-list card">
        <h3 class="list-title">最近信号</h3>
        <div v-if="!signals.length" class="list-empty">暂无信号记录</div>
        <div
          v-for="s in signals"
          :key="s.id"
          class="signal-item"
          :class="{ active: selected?.id === s.id }"
          @click="selectSignal(s)"
        >
          <div class="signal-symbol">{{ s.symbol }}</div>
          <div class="signal-meta">
            <span>{{ s.indicator }}</span>
            <span>{{ s.timeframe }}</span>
            <span class="signal-time">{{ formatTime(s.triggered_at) }}</span>
          </div>
          <span v-if="s.has_analysis" class="badge badge-success" style="font-size: 11px">已分析</span>
        </div>
      </div>

      <!-- Analysis Panel -->
      <div class="analysis-panel card">
        <div v-if="!selected" class="empty-state" style="padding: 40px">
          <h3>选择一个信号</h3>
          <p>从左侧列表选择信号进行 AI 分析</p>
        </div>

        <div v-else>
          <!-- Signal Info -->
          <div class="signal-info-card">
            <div class="info-row">
              <span class="info-label">币种</span>
              <span class="info-value">{{ detail?.signal?.symbol }} ({{ detail?.signal?.exchange }})</span>
            </div>
            <div class="info-row">
              <span class="info-label">指标</span>
              <span class="info-value">{{ detail?.signal?.indicator }} / {{ detail?.signal?.timeframe }}</span>
            </div>
            <div v-if="detail?.snapshot" class="info-row">
              <span class="info-label">触发价格</span>
              <span class="info-value">{{ detail.snapshot.price }}</span>
            </div>
            <div v-if="detail?.snapshot" class="info-row">
              <span class="info-label">24h涨跌</span>
              <span class="info-value" :class="detail.snapshot.change_24h > 0 ? 'clr-positive' : 'clr-negative'">
                {{ detail.snapshot.change_24h?.toFixed(2) }}%
              </span>
            </div>
            <div v-if="detail?.history?.length" class="info-row">
              <span class="info-label">历史触发</span>
              <span class="info-value">{{ detail.history.length }} 次记录</span>
            </div>
          </div>

          <!-- AI Analysis -->
          <div class="analysis-section">
            <div class="analysis-header">
              <h3>🤖 AI 分析</h3>
              <button class="btn btn-sm" @click="runAnalysis" :disabled="streaming">
                {{ streaming ? '分析中...' : (selected.has_analysis ? '重新分析' : '生成分析') }}
              </button>
            </div>

            <div v-if="analysisText" class="analysis-content">
              <div class="analysis-text" v-html="renderMarkdown(analysisText)"></div>
              <div v-if="sentiment" class="sentiment-badge">
                <span class="badge" :class="sentimentClass">{{ sentimentLabel }}</span>
              </div>
            </div>

            <div v-if="!analysisText && !streaming" class="analysis-empty">
              点击"生成分析"让 AI 解读此信号
            </div>

            <div v-if="analysisError" class="analysis-error">{{ analysisError }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api/client.js'

const signals = ref([])
const selected = ref(null)
const detail = ref(null)
const analysisText = ref('')
const sentiment = ref('')
const streaming = ref(false)
const analysisError = ref('')

const sentimentClass = computed(() => ({
  'badge-success': sentiment.value === 'bullish',
  'badge-danger': sentiment.value === 'bearish',
}))

const sentimentLabel = computed(() => {
  if (sentiment.value === 'bullish') return '看涨'
  if (sentiment.value === 'bearish') return '看跌'
  return '中性'
})

function formatTime(t) {
  if (!t) return ''
  return t.replace('T', ' ').substring(5, 16)
}

function renderMarkdown(text) {
  return text
    .replace(/\n/g, '<br>')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/⚠️/g, '<span style="color:var(--warning)">⚠️</span>')
}

async function loadSignals() {
  try {
    signals.value = await api.getAISignals()
  } catch {}
}

async function selectSignal(s) {
  selected.value = s
  analysisText.value = s.analysis_text || ''
  sentiment.value = s.sentiment || ''
  analysisError.value = ''

  try {
    detail.value = await api.getSignalDetail(s.id)
    if (detail.value.analysis) {
      analysisText.value = detail.value.analysis.text
      sentiment.value = detail.value.analysis.sentiment
    }
  } catch {}
}

async function runAnalysis() {
  if (!selected.value) return
  streaming.value = true
  analysisText.value = ''
  sentiment.value = ''
  analysisError.value = ''

  try {
    const resp = await fetch(`/api/ai/analyze/${selected.value.id}`, { method: 'POST' })
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const payload = line.slice(6).trim()
        if (payload === '[DONE]') continue
        try {
          const data = JSON.parse(payload)
          if (data.error) {
            analysisError.value = data.error
          } else if (data.text) {
            analysisText.value += data.text
          }
        } catch {}
      }
    }

    // Detect sentiment from final text
    const lower = analysisText.value.toLowerCase()
    if (['看涨', '偏多', 'bullish'].some(w => lower.includes(w))) sentiment.value = 'bullish'
    else if (['看跌', '偏空', 'bearish'].some(w => lower.includes(w))) sentiment.value = 'bearish'
    else sentiment.value = 'neutral'

    // Mark as analyzed in list
    if (selected.value) selected.value.has_analysis = true
  } catch (e) {
    analysisError.value = '分析失败: ' + e.message
  } finally {
    streaming.value = false
  }
}

onMounted(loadSignals)
</script>

<style scoped>
.ai-layout {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 20px;
  min-height: 500px;
}

.signal-list {
  overflow-y: auto;
  max-height: 70vh;
  padding: 16px;
}

.list-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 12px;
}

.list-empty {
  color: var(--text-tertiary);
  font-size: 13px;
  text-align: center;
  padding: 20px;
}

.signal-item {
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--transition-fast);
  margin-bottom: 4px;
}

.signal-item:hover {
  background: var(--bg-tertiary);
}

.signal-item.active {
  background: var(--accent-subtle);
}

.signal-symbol {
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 4px;
}

.signal-meta {
  display: flex;
  gap: 8px;
  font-size: 12px;
  color: var(--text-tertiary);
}

.signal-time {
  margin-left: auto;
}

.analysis-panel {
  padding: 24px;
}

.signal-info-card {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  padding: 16px;
  background: var(--bg-primary);
  border-radius: var(--radius-md);
  margin-bottom: 20px;
}

.info-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.info-label {
  font-size: 11px;
  color: var(--text-tertiary);
  font-weight: 600;
  letter-spacing: 0.03em;
}

.info-value {
  font-size: 14px;
  font-weight: 500;
}

.analysis-section {
  margin-top: 8px;
}

.analysis-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.analysis-header h3 {
  font-size: 16px;
}

.analysis-content {
  padding: 16px;
  background: var(--bg-primary);
  border-radius: var(--radius-md);
  line-height: 1.7;
  font-size: 14px;
}

.analysis-text {
  white-space: pre-wrap;
}

.sentiment-badge {
  margin-top: 12px;
}

.analysis-empty {
  color: var(--text-tertiary);
  font-size: 14px;
  text-align: center;
  padding: 32px;
}

.analysis-error {
  color: var(--danger);
  font-size: 13px;
  margin-top: 12px;
}

.clr-positive { color: var(--success); }
.clr-negative { color: var(--danger); }
</style>

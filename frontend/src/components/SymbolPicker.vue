<template>
  <div class="sym-picker" ref="rootEl">
    <input
      ref="inputEl"
      :value="modelValue"
      :placeholder="placeholder"
      :disabled="disabled"
      class="sym-input"
      autocomplete="off"
      spellcheck="false"
      @input="onInput"
      @focus="openList"
      @keydown="onKeydown"
    />
    <span v-if="loading" class="sym-status" title="正在加载标的列表">⋯</span>
    <button
      v-else-if="modelValue"
      type="button"
      class="sym-clear"
      title="清空"
      @click="clear"
    >×</button>

    <div v-if="open && matches.length" class="sym-list">
      <button
        v-for="(m, i) in matches"
        :key="m.symbol"
        type="button"
        class="sym-opt"
        :class="{ active: i === cursor }"
        @mousedown.prevent="choose(m)"
        @mouseenter="cursor = i"
      >
        <span class="sym-name">{{ m.symbol }}</span>
        <span class="sym-chg" :class="m.priceChangePercent >= 0 ? 'up' : 'down'">
          {{ m.priceChangePercent >= 0 ? '+' : '' }}{{ m.priceChangePercent.toFixed(2) }}%
        </span>
        <span class="sym-vol">{{ fmtVol(m.volume24h) }}</span>
      </button>
    </div>

    <!-- 列表外的输入是合法的：ChartShot 也认 OANDA:XAUUSD 这类标的，
         所以只提示、不拦截 -->
    <p v-if="open && modelValue && !matches.length && symbols.length" class="sym-note">
      不在合约列表中，将按原样提交
    </p>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api/client.js'

const props = defineProps({
  modelValue: { type: String, default: '' },
  placeholder: { type: String, default: 'BTCUSDT' },
  disabled: { type: Boolean, default: false },
  maxItems: { type: Number, default: 8 },
})
const emit = defineEmits(['update:modelValue', 'select', 'enter'])

const symbols = ref([])
const loading = ref(false)
const open = ref(false)
const cursor = ref(0)
const rootEl = ref(null)
const inputEl = ref(null)

const matches = computed(() => {
  const q = (props.modelValue || '').trim().toUpperCase()
  if (!symbols.value.length) return []
  // 空输入时给成交量最大的几个作为起点
  if (!q) return symbols.value.slice(0, props.maxItems)
  // 前缀命中排在子串命中之前——输 "BTC" 时 BTCUSDT 应该在 WBTCUSDT 上面
  const prefix = [], substr = []
  for (const s of symbols.value) {
    const idx = s.symbol.indexOf(q)
    if (idx === 0) prefix.push(s)
    else if (idx > 0) substr.push(s)
    if (prefix.length >= props.maxItems) break
  }
  return [...prefix, ...substr].slice(0, props.maxItems)
})

function fmtVol(v) {
  if (!v) return '-'
  if (v >= 1e9) return (v / 1e9).toFixed(1) + 'B'
  if (v >= 1e6) return (v / 1e6).toFixed(0) + 'M'
  if (v >= 1e3) return (v / 1e3).toFixed(0) + 'K'
  return String(Math.round(v))
}

function onInput(e) {
  // 标的代码不含空格，直接 trim 掉——粘贴带空格的值很常见
  emit('update:modelValue', e.target.value.toUpperCase().trim())
  cursor.value = 0
  open.value = true
}

function openList() {
  open.value = true
  cursor.value = 0
}

function choose(m) {
  emit('update:modelValue', m.symbol)
  emit('select', m)
  open.value = false
}

function clear() {
  emit('update:modelValue', '')
  open.value = false
  inputEl.value?.focus()
}

function onKeydown(e) {
  if (e.key === 'Escape') { open.value = false; return }
  if (e.key === 'Enter') {
    // 下拉开着且有高亮项时 Enter 是「选中」，否则透传给父组件（直接提交）
    if (open.value && matches.value.length) {
      choose(matches.value[cursor.value])
    } else {
      emit('enter')
    }
    return
  }
  if (!matches.value.length) return
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    open.value = true
    cursor.value = (cursor.value + 1) % matches.value.length
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    open.value = true
    cursor.value = (cursor.value - 1 + matches.value.length) % matches.value.length
  }
}

// 点击组件外部才收起：用 mousedown.prevent 处理选项点击，避免 blur 抢跑
function onDocClick(e) {
  if (rootEl.value && !rootEl.value.contains(e.target)) open.value = false
}

onMounted(async () => {
  document.addEventListener('click', onDocClick)
  loading.value = true
  try {
    const res = await api.listSymbols()
    symbols.value = res.symbols || []
  } catch {
    symbols.value = []   // 取不到就退化成纯文本输入
  } finally {
    loading.value = false
  }
})

onBeforeUnmount(() => document.removeEventListener('click', onDocClick))
</script>

<style scoped>
.sym-picker { position: relative; }
/* 右侧留位给清空按钮/加载提示，避免和输入文字重叠（全局 input 内边距是 11px 16px）。
   留得越少越好——工具栏里这个框只有 140px 宽。 */
.sym-input { width: 100%; text-transform: uppercase; padding-right: 30px; }
.sym-status, .sym-clear {
  position: absolute; right: 10px; top: 50%; transform: translateY(-50%);
  font-size: 13px; color: var(--text-secondary); pointer-events: none;
}
.sym-clear {
  pointer-events: auto; cursor: pointer; border: none; background: none;
  font-size: 16px; line-height: 1; padding: 0 2px;
}
.sym-clear:hover { color: var(--text-primary, inherit); }

.sym-list {
  position: absolute; z-index: 30; left: 0; right: 0; top: calc(100% + 4px);
  /* 工具栏里的输入框可能只有 140px，下拉要能超出它展开，否则
     「BTCUSDT +0.95% 2.63B」三段会挤成一团 */
  min-width: 240px;
  max-height: 260px; overflow-y: auto; border-radius: 8px; padding: 4px;
  border: 1px solid var(--border-strong, rgba(128,128,128,.3));
  background: var(--bg-secondary, #1c1c1c);
  box-shadow: 0 6px 18px rgba(0,0,0,.28);
}
.sym-opt {
  display: flex; align-items: center; gap: 8px; width: 100%;
  padding: 5px 10px; border: none; background: none; color: inherit;
  border-radius: 6px; cursor: pointer; font-size: 12.5px; text-align: left;
}
.sym-opt.active { background: var(--accent-subtle, rgba(200,110,60,.22)); }
.sym-name { font-family: monospace; font-weight: 600; }
.sym-chg { margin-left: auto; font-variant-numeric: tabular-nums; }
.sym-chg.up { color: var(--success, #3fa66a); }
.sym-chg.down { color: var(--danger, #d9534f); }
.sym-vol {
  min-width: 46px; text-align: right; color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}
.sym-note { margin: 6px 0 0; font-size: 11.5px; color: var(--text-secondary); }
</style>

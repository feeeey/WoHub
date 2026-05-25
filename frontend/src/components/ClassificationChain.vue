<template>
  <div v-if="visible.length" class="chain">
    <template v-for="(item, idx) in visible" :key="item.key">
      <span class="chain-chip" :class="chipClass(item)">{{ item.value }}</span>
      <span v-if="idx < visible.length - 1" class="chain-sep">→</span>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  classification: { type: Object, required: true },
  enabled: { type: Object, required: true }, // { L0: bool, L1: bool, L2: bool, L3: bool }
})

const visible = computed(() => {
  const out = []
  if (props.enabled.L0) out.push({ key: 'L0', value: props.classification.l0 })
  if (props.enabled.L1) out.push({ key: 'L1', value: props.classification.l1 })
  if (props.enabled.L2) out.push({ key: 'L2', value: props.classification.l2 })
  if (props.enabled.L3) out.push({ key: 'L3', value: props.classification.l3 })
  return out
})

function chipClass(item) {
  // L0 and L1 colored by 阳/阴 in the value; L2 by 看涨/看跌; L3 neutral.
  const v = item.value
  if (item.key === 'L0') return v.startsWith('阳') ? 'chip-bull' : 'chip-bear'
  if (item.key === 'L1') {
    if (v === '阳线') return 'chip-bull'
    if (v === '阴线') return 'chip-bear'
    return 'chip-neutral'
  }
  if (item.key === 'L2') {
    if (v === '看涨') return 'chip-bull'
    if (v === '看跌') return 'chip-bear'
    return 'chip-neutral'
  }
  return 'chip-neutral'
}
</script>

<style scoped>
.chain {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}

.chain-chip {
  display: inline-flex;
  align-items: center;
  padding: 3px 10px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.01em;
  white-space: nowrap;
}

.chain-sep {
  color: var(--text-tertiary);
  font-size: 12px;
  user-select: none;
}

.chip-bull { background: var(--success-subtle); color: var(--success); }
.chip-bear { background: var(--danger-subtle); color: var(--danger); }
.chip-neutral { background: var(--bg-tertiary); color: var(--text-secondary); }
</style>

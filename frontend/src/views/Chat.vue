<!-- frontend/src/views/Chat.vue -->
<template>
  <div class="chat-page">
    <!-- ======== 会话侧栏 ======== -->
    <aside class="chat-side">
      <button class="new-chat" @click="newSession">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14" /></svg>
        新会话
      </button>

      <nav class="sess-list">
        <template v-for="g in sessionGroups" :key="g.label">
          <div class="sess-group">{{ g.label }}</div>
          <div v-for="s in g.items" :key="s.id" class="sess-item"
               :class="{ active: s.id === activeId }" @click="selectSession(s.id)">
            <input v-if="editingId === s.id" ref="editInputs" v-model="editTitle" class="sess-edit"
                   @click.stop @keydown.enter.prevent="commitRename(s)"
                   @keydown.esc="cancelRename" @blur="commitRename(s)" />
            <template v-else>
              <span class="sess-title">{{ s.title }}</span>
              <span class="sess-ops" @click.stop>
                <button title="重命名" @click="startRename(s)">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" /></svg>
                </button>
                <button class="op-del" title="删除" @click="removeSession(s)">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" /><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
                </button>
              </span>
            </template>
          </div>
        </template>
        <div v-if="!sessions.length" class="sess-none">对话记录会出现在这里</div>
      </nav>
    </aside>

    <!-- ======== 主区 ======== -->
    <section class="chat-main">
      <header v-if="!showHero" class="chat-topbar">
        <span class="topbar-title">{{ activeSession?.title || '对话' }}</span>
        <span class="topbar-meta">{{ messages.length }} 条消息</span>
      </header>

      <div class="chat-body" :class="{ hero: showHero }">
        <!-- 空状态：问候 + 居中输入 -->
        <div v-if="showHero" class="hero-block">
          <svg class="hero-mark" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M12 2.5v19M2.5 12h19M5.3 5.3l13.4 13.4M18.7 5.3 5.3 18.7" /></svg>
          <h1 class="hero-greet">{{ greeting }}</h1>
          <p class="hero-sub">给我一个标的或方向，直接开始纯技术面分析</p>
        </div>

        <!-- 消息流 -->
        <div v-else ref="scrollEl" class="chat-scroll" @scroll="onScroll">
          <div class="msg-col">
            <div v-for="m in messages" :key="m.id" class="msg" :class="m.role">
              <!-- 用户消息：右侧气泡 -->
              <div v-if="m.role === 'user'" class="user-bubble">
                <div v-if="m.images && m.images.length" class="msg-images">
                  <a v-for="img in m.images" :key="img.filename"
                     :href="api.chatImageUrl(img.kind, img.filename)" target="_blank">
                    <img :src="api.chatImageUrl(img.kind, img.filename)" />
                  </a>
                </div>
                <div class="plain">{{ m.content }}</div>
              </div>

              <!-- 助手消息：无气泡纯排版 -->
              <div v-else class="assistant-turn">
                <details v-if="traceSteps(m).length" class="trace">
                  <summary>
                    <svg class="t-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10" /></svg>
                    {{ traceSteps(m).length }} 个工具调用
                    <svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
                  </summary>
                  <div class="tool-rows">
                    <details v-for="(st, i) in traceSteps(m)" :key="i" class="tool-row">
                      <summary class="tool-head">
                        <span class="tool-dot done"></span>
                        <span class="tool-label">{{ toolLabel(st.tool) }}</span>
                        <span class="tool-note">{{ shortJson(st.args) }}</span>
                        <svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
                      </summary>
                      <pre class="tool-pre">{{ st.result }}</pre>
                    </details>
                  </div>
                </details>

                <div v-if="m.images && m.images.length" class="msg-images">
                  <a v-for="img in m.images" :key="img.filename"
                     :href="api.chatImageUrl(img.kind, img.filename)" target="_blank">
                    <img :src="api.chatImageUrl(img.kind, img.filename)" />
                  </a>
                </div>

                <div class="md" v-html="renderMd(m.content)"></div>

                <div v-if="m.error" class="msg-error" :class="{ soft: m.error === 'cancelled' }">
                  <span>{{ m.error === 'cancelled' ? '已停止生成' : '出错了：' + m.error }}</span>
                  <button v-if="retryTargetOf(m)" class="retry-btn" @click="retry(m)">重试</button>
                </div>

                <div class="msg-actions">
                  <button class="action-btn" @click="copyMsg(m)">
                    <svg v-if="copiedId === m.id" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12" /></svg>
                    <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></svg>
                    {{ copiedId === m.id ? '已复制' : '复制' }}
                  </button>
                </div>
              </div>
            </div>

            <!-- 进行中的轮次 -->
            <div v-if="live.active" class="msg assistant">
              <div class="assistant-turn">
                <div v-if="live.cards.length" class="tool-rows live">
                  <div v-for="(c, i) in live.cards" :key="i" class="tool-row" :class="c.status">
                    <div class="tool-head" :class="{ clickable: c.summary }"
                         @click="c.summary ? (c.open = !c.open) : null">
                      <span class="tool-dot" :class="c.status"></span>
                      <span class="tool-label">{{ toolLabel(c.tool) }}</span>
                      <span class="tool-note">{{ c.note }}</span>
                      <span v-if="c.elapsed" class="tool-ms">{{ fmtMs(c.elapsed) }}</span>
                      <svg v-if="c.summary" class="chev" :class="{ open: c.open }" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
                    </div>
                    <pre v-if="c.open" class="tool-pre">{{ c.summary }}</pre>
                  </div>
                </div>

                <div v-if="live.images.length" class="msg-images">
                  <a v-for="img in live.images" :key="img.filename"
                     :href="api.chatImageUrl(img.kind, img.filename)" target="_blank">
                    <img :src="api.chatImageUrl(img.kind, img.filename)" />
                  </a>
                </div>

                <div v-if="live.text" class="md" v-html="renderMd(live.text)"></div>
                <span v-if="live.text" class="stream-cursor"></span>

                <div v-if="!live.text && !live.cards.length" class="thinking">
                  <svg class="think-mark" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M12 2.5v19M2.5 12h19M5.3 5.3l13.4 13.4M18.7 5.3 5.3 18.7" /></svg>
                  正在分析…
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 输入区 -->
        <div class="composer-zone">
          <button v-if="!showHero && !atBottom" class="scroll-pill" title="回到底部" @click="scrollBottom(true)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14" /><path d="m19 12-7 7-7-7" /></svg>
          </button>

          <div class="composer">
            <div v-if="pendingFiles.length" class="pend-imgs">
              <span v-for="(f, i) in pendingFiles" :key="i" class="pend-chip">
                <img :src="f.preview" />
                <button title="移除" @click="removeFile(i)">✕</button>
              </span>
            </div>
            <textarea ref="taEl" v-model="draft" rows="1"
                      placeholder="问点什么…（Shift + Enter 换行）"
                      @keydown.enter.exact="onEnterKey" @paste="onPaste" @input="autoGrow"></textarea>
            <div class="composer-bar">
              <button class="icon-btn" title="添加图片（PNG / JPEG，≤5MB）" @click="fileInput.click()">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8.5" cy="8.5" r="1.5" /><path d="m21 15-5-5L5 21" /></svg>
              </button>
              <input ref="fileInput" type="file" accept="image/png,image/jpeg" multiple
                     style="display:none" @change="pickFiles" />
              <span class="bar-space"></span>
              <button v-if="live.active" class="send-btn" title="停止生成" @click="stop">
                <svg viewBox="0 0 24 24" fill="currentColor"><rect x="7" y="7" width="10" height="10" rx="1.5" /></svg>
              </button>
              <button v-else class="send-btn" :disabled="!canSend" title="发送" @click="send">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5" /><path d="m5 12 7-7 7 7" /></svg>
              </button>
            </div>
          </div>

          <div v-if="showHero" class="hero-chips">
            <button v-for="c in promptChips" :key="c" class="chip" @click="useChip(c)">{{ c }}</button>
          </div>
          <p v-else class="composer-note">AI 生成内容仅供参考，不构成投资建议</p>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, reactive, computed, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { api } from '../api/client.js'

const sessions = ref([])
const activeId = ref(null)
const messages = ref([])
const draft = ref('')
const scrollEl = ref(null)
const taEl = ref(null)
const pendingFiles = ref([])
const fileInput = ref(null)
const live = reactive({ active: false, turnId: null, text: '', cards: [], images: [] })

const loadingSession = ref(false)
const atBottom = ref(true)
const editingId = ref(null)
const editTitle = ref('')
const editInputs = ref([])
const copiedId = ref(null)
let copiedTimer = null

let es = null
let reconnectTimer = null
let lastEventId = 0
let reconnectDelay = 1000

const TOOL_LABELS = {
  market_snapshot: '行情快照',
  kline_summary: 'K线摘要',
  signal_history: '信号历史',
  position_plan_preview: '仓位试算',
  get_klines: '拉取K线',
  get_indicators: '计算指标',
  list_watchlists: '关注列表',
  market_overview: '市场总览',
  run_screener_scan: '筛选器扫描',
  capture_chart: '图表截图',
  account_overview: '账户概览',
}

const promptChips = [
  '扫一遍关注列表，找顶底背离',
  'BTC 4 小时结构怎么看？',
  'ETH 想做多，帮我算仓位',
  '今天市场整体什么情况？',
]

// ---- 派生状态 ----
const showHero = computed(() =>
  !loadingSession.value && (!activeId.value || (!messages.value.length && !live.active)))

const activeSession = computed(() => sessions.value.find(s => s.id === activeId.value))

const canSend = computed(() => !!draft.value.trim() || pendingFiles.value.length > 0)

const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 5) return '夜深了'
  if (h < 12) return '早上好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
})

const sessionGroups = computed(() => {
  const buckets = [['今天', []], ['昨天', []], ['近 7 天', []], ['更早', []]]
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  for (const s of sessions.value) {
    // SQLite datetime('now') 为 UTC，补 Z 后按本地时区分桶
    const d = new Date(String(s.updated_at || '').replace(' ', 'T') + 'Z')
    let idx = 3
    if (!isNaN(d)) {
      const day = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
      const diff = Math.round((today - day) / 86400000)
      idx = diff <= 0 ? 0 : diff === 1 ? 1 : diff <= 7 ? 2 : 3
    }
    buckets[idx][1].push(s)
  }
  return buckets.filter(([, items]) => items.length).map(([label, items]) => ({ label, items }))
})

// ---- 工具函数 ----
function renderMd(text) {
  return DOMPurify.sanitize(marked.parse(text || ''))
}
function toolLabel(name) {
  return TOOL_LABELS[name] || name
}
function fmtMs(ms) {
  return ms >= 1000 ? (ms / 1000).toFixed(1) + 's' : ms + 'ms'
}
function shortJson(o) {
  try { const s = JSON.stringify(o); return s.length > 120 ? s.slice(0, 120) + '…' : s }
  catch { return '' }
}
function traceSteps(m) {
  return (m.trace && m.trace.steps ? m.trace.steps : []).filter(s => s.tool)
}
function retryTargetOf(m) {
  // 失败/停止的 assistant 消息 → 找它前面最近的 user 消息
  if (m.role !== 'assistant' || !m.error || live.active) return null
  const idx = messages.value.findIndex(x => x.id === m.id)
  for (let i = idx - 1; i >= 0; i--) {
    if (messages.value[i].role === 'user') return messages.value[i]
  }
  return null
}

async function copyMsg(m) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(m.content)
    } else {
      const ta = document.createElement('textarea')
      ta.value = m.content
      ta.style.position = 'fixed'; ta.style.opacity = '0'
      document.body.appendChild(ta); ta.select()
      document.execCommand('copy'); ta.remove()
    }
    copiedId.value = m.id
    if (copiedTimer) clearTimeout(copiedTimer)
    copiedTimer = setTimeout(() => { copiedId.value = null }, 1500)
  } catch {}
}

// ---- 滚动 ----
function onScroll() {
  const el = scrollEl.value
  if (el) atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 120
}
async function scrollBottom(force = false) {
  if (!force && !atBottom.value) return
  await nextTick()
  if (scrollEl.value) {
    scrollEl.value.scrollTop = scrollEl.value.scrollHeight
    atBottom.value = true
  }
}

// ---- 输入框 ----
function autoGrow() {
  const el = taEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 220) + 'px'
}
function useChip(text) {
  draft.value = text
  nextTick(() => { autoGrow(); taEl.value && taEl.value.focus() })
}
function addFile(file) {
  if (!['image/png', 'image/jpeg'].includes(file.type)) { alert('仅支持 PNG/JPEG'); return }
  if (file.size > 5 * 1024 * 1024) { alert('图片超过 5MB'); return }
  pendingFiles.value.push({ file, preview: URL.createObjectURL(file) })
}
function pickFiles(e) {
  for (const f of e.target.files) addFile(f)
  e.target.value = ''
}
function onPaste(e) {
  for (const item of e.clipboardData.items) {
    if (item.type.startsWith('image/')) { const f = item.getAsFile(); if (f) addFile(f) }
  }
}
function removeFile(i) {
  URL.revokeObjectURL(pendingFiles.value[i].preview)
  pendingFiles.value.splice(i, 1)
}
function onEnterKey(e) {
  // 输入法组词中：Enter 只确认候选词，不发送（isComposing 标准；229 兼容旧 IME 事件）
  if (e.isComposing || e.keyCode === 229) return
  if (live.active) return              // 生成中：Enter 落为换行，不触发发送
  e.preventDefault()
  send()
}

function resetLive() {
  live.active = false
  live.turnId = null
  live.text = ''
  live.cards = []
  live.images = []
}

// ---- SSE ----
function closeStream() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
  if (es) { es.close(); es = null }
}

// 单一事件应用逻辑：实时 on() 与断线重连后的历史回放（replayEvent）共用，
// 避免同一套 text_delta/tool_start/… 处理分叉成两份容易漂移的代码。
function replayEvent(type, p) {
  if (!p || p._truncated) return
  if (type === 'text_delta') { live.active = true; live.text += p.text }
  else if (type === 'tool_start') {
    live.active = true
    live.cards.push({ tool: p.tool, status: 'running', note: shortJson(p.args), summary: '', elapsed: 0, open: false })
  } else if (type === 'tool_progress') {
    const c = [...live.cards].reverse().find(c => c.tool === p.tool && c.status === 'running')
    if (c) c.note = `${p.done}/${p.total} · ${p.note}`
  } else if (type === 'tool_end') {
    const c = [...live.cards].reverse().find(c => c.tool === p.tool && c.status === 'running')
    if (c) { c.status = p.ok ? 'done' : 'error'; c.summary = p.summary; c.elapsed = p.elapsed_ms }
  } else if (type === 'image') {
    live.images.push({ kind: p.kind, filename: p.filename })
  }
}

function openStream() {
  closeStream()
  if (!activeId.value) return
  es = new EventSource(api.chatStreamUrl(activeId.value, lastEventId))
  const on = (type, fn) => es.addEventListener(type, e => {
    lastEventId = Number(e.lastEventId || lastEventId)
    reconnectDelay = 1000
    fn(JSON.parse(e.data))
    scrollBottom()
  })
  on('text_delta', p => replayEvent('text_delta', p))
  on('tool_start', p => replayEvent('tool_start', p))
  on('tool_progress', p => replayEvent('tool_progress', p))
  on('tool_end', p => replayEvent('tool_end', p))
  on('image', p => replayEvent('image', p))
  on('turn_done', async () => { await finalizeTurn() })
  on('turn_error', async () => { await finalizeTurn() })
  on('cancelled', async () => { await finalizeTurn() })
  es.onerror = () => {
    closeStream()
    reconnectTimer = setTimeout(openStream, reconnectDelay)
    reconnectDelay = Math.min(reconnectDelay * 2, 10000)
  }
}

async function finalizeTurn() {
  const data = await api.getChatMessages(activeId.value)
  messages.value = data.messages
  resetLive()
  await loadSessions()          // 标题可能被自动更新
  scrollBottom()
}

// ---- 会话 ----
async function loadSessions() {
  sessions.value = await api.listChatSessions()
}

async function selectSession(id) {
  if (editingId.value && editingId.value !== id) cancelRename()
  activeId.value = id
  resetLive()
  loadingSession.value = true
  let data
  try {
    data = await api.getChatMessages(id)
  } finally {
    loadingSession.value = false
  }
  messages.value = data.messages
  // 游标取自最后一条回放事件本身，避免用（晚查询、可能更大的）全局 last_event_id
  // 导致跟播时漏掉回放与开流之间产生的事件
  lastEventId = (data.active_turn && data.active_events.length)
    ? data.active_events[data.active_events.length - 1].id
    : data.last_event_id
  if (data.active_turn) {
    // 恢复进行中轮次：先回放已有事件，再从 lastEventId 跟播
    live.active = true
    live.turnId = data.active_turn.id
    for (const ev of data.active_events) replayEvent(ev.type, ev.payload)
  }
  openStream()
  atBottom.value = true
  scrollBottom(true)
}

function newSession() {
  // 会话推迟到首次发送时才真正创建，避免留下空会话
  closeStream()
  activeId.value = null
  messages.value = []
  resetLive()
  nextTick(() => taEl.value && taEl.value.focus())
}

function startRename(s) {
  editingId.value = s.id
  editTitle.value = s.title
  nextTick(() => {
    const el = editInputs.value && editInputs.value[0]
    if (el) { el.focus(); el.select() }
  })
}
function cancelRename() {
  editingId.value = null
  editTitle.value = ''
}
async function commitRename(s) {
  if (editingId.value !== s.id) return
  const t = editTitle.value.trim()
  cancelRename()
  if (t && t !== s.title) {
    await api.renameChatSession(s.id, t)
    await loadSessions()
  }
}

async function removeSession(s) {
  if (!confirm(`删除会话「${s.title}」及全部消息？`)) return
  await api.deleteChatSession(s.id)
  if (activeId.value === s.id) { activeId.value = null; messages.value = []; resetLive(); closeStream() }
  await loadSessions()
}

// ---- 发送/停止/重试 ----
async function send() {
  const text = draft.value.trim()
  const pend = pendingFiles.value
  const files = pend.map(p => p.file)
  if ((!text && !files.length) || live.active) return
  draft.value = ''
  pendingFiles.value = []
  nextTick(autoGrow)
  try {
    if (!activeId.value) {
      const { id } = await api.createChatSession()
      await loadSessions()
      await selectSession(id)
    }
    const r = await api.sendChatMessage(activeId.value, text, files)
    pend.forEach(p => URL.revokeObjectURL(p.preview))   // 预览用途已尽，回收
    const data = await api.getChatMessages(activeId.value)   // 拿服务端落库的 images 引用
    messages.value = data.messages
    live.active = true
    live.turnId = r.turn_id
    scrollBottom(true)
  } catch (e) {
    alert('发送失败：' + e.message)
    draft.value = text
    pendingFiles.value = pend                            // 附件与草稿一并恢复（预览未回收仍可用）
    nextTick(autoGrow)
  }
}

async function stop() {
  if (live.turnId) { try { await api.cancelChatTurn(live.turnId) } catch {} }
}

async function retry(m) {
  const target = retryTargetOf(m)
  if (!target) return
  try {
    const r = await api.retryChatMessage(target.id)
    live.active = true
    live.turnId = r.turn_id
  } catch (e) { alert('重试失败：' + e.message) }
}

onMounted(async () => {
  await loadSessions()
  if (sessions.value.length) await selectSession(sessions.value[0].id)
})
onBeforeUnmount(() => {
  closeStream()
  if (copiedTimer) clearTimeout(copiedTimer)
  pendingFiles.value.forEach(p => URL.revokeObjectURL(p.preview))
})
</script>

<style scoped>
/* ======== 布局骨架 ======== */
.chat-page { display: flex; height: 100%; background: var(--bg-base); }

/* ======== 会话侧栏 ======== */
.chat-side {
  width: 264px; flex-shrink: 0;
  display: flex; flex-direction: column; gap: 10px;
  padding: 16px 12px 12px;
  background: var(--bg-primary);
  border-right: 1px solid var(--border);
}
.new-chat {
  display: flex; align-items: center; justify-content: center; gap: 7px;
  width: 100%; padding: 9px 12px;
  border: 1px solid var(--border-strong); border-radius: var(--radius-md);
  background: transparent; color: var(--accent);
  font: inherit; font-size: 13.5px; font-weight: 600; letter-spacing: -0.01em;
  cursor: pointer; transition: all var(--transition-fast);
}
.new-chat:hover { background: var(--accent-subtle); border-color: var(--accent-muted); }
.new-chat svg { width: 15px; height: 15px; }

.sess-list { flex: 1; overflow-y: auto; overflow-x: hidden; min-height: 0; }
.sess-group {
  padding: 14px 10px 6px;
  font-size: 11px; font-weight: 600; letter-spacing: 0.06em;
  color: var(--text-tertiary);
}
.sess-item {
  display: flex; justify-content: space-between; align-items: center; gap: 6px;
  height: 36px; padding: 0 10px; border-radius: var(--radius-sm);
  cursor: pointer; font-size: 13.5px; color: var(--text-secondary);
  transition: background var(--transition-fast), color var(--transition-fast);
}
.sess-item:hover { background: var(--bg-tertiary); color: var(--text-primary); }
.sess-item.active { background: var(--accent-subtle); color: var(--accent); }
.sess-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sess-ops { display: none; flex-shrink: 0; gap: 2px; }
.sess-item:hover .sess-ops { display: flex; }
.sess-ops button {
  display: flex; align-items: center; justify-content: center;
  width: 24px; height: 24px; border: none; border-radius: 6px;
  background: none; color: var(--text-tertiary); cursor: pointer;
  transition: all var(--transition-fast);
}
.sess-ops button:hover { background: var(--bg-elevated); color: var(--text-primary); }
.sess-ops button.op-del:hover { color: var(--danger); }
.sess-ops svg { width: 14px; height: 14px; }
.sess-edit {
  width: 100%; padding: 4px 8px; font: inherit; font-size: 13px;
  background: var(--bg-primary); color: var(--text-primary);
  border: 1px solid var(--accent); border-radius: 6px; outline: none;
  box-shadow: 0 0 0 3px var(--accent-subtle);
}
.sess-none { padding: 20px 10px; font-size: 12.5px; color: var(--text-tertiary); text-align: center; }

/* ======== 主区 ======== */
.chat-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.chat-topbar {
  flex-shrink: 0; height: 52px;
  display: flex; align-items: center; gap: 12px; padding: 0 24px;
  border-bottom: 1px solid var(--border);
}
.topbar-title {
  font-size: 14px; font-weight: 600; letter-spacing: -0.01em;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.topbar-meta { flex-shrink: 0; margin-left: auto; font-size: 12px; color: var(--text-tertiary); }

.chat-body { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.chat-body.hero { justify-content: center; padding-bottom: 8vh; }

/* ======== 空状态 ======== */
.hero-block { text-align: center; padding: 0 24px; margin-bottom: 30px; }
.hero-mark {
  width: 42px; height: 42px; color: var(--accent);
  margin-bottom: 20px;
  animation: heroIn 0.7s cubic-bezier(0.16, 1, 0.3, 1);
}
.hero-greet {
  font-family: var(--font-display);
  font-size: 34px; font-weight: 500; letter-spacing: 0.01em;
  color: var(--text-primary); line-height: 1.3;
}
.hero-sub { margin-top: 10px; font-size: 15px; color: var(--text-secondary); }
@keyframes heroIn {
  from { opacity: 0; transform: scale(0.6) rotate(-30deg); }
  to { opacity: 1; transform: scale(1) rotate(0); }
}
.hero-chips {
  display: flex; flex-wrap: wrap; justify-content: center; gap: 8px;
  max-width: 640px; margin: 18px auto 0;
}
.chip {
  padding: 8px 15px; border: 1px solid var(--border-strong); border-radius: 999px;
  background: transparent; color: var(--text-secondary);
  font: inherit; font-size: 13px; cursor: pointer;
  transition: all var(--transition-fast);
}
.chip:hover { background: var(--bg-secondary); color: var(--text-primary); box-shadow: var(--shadow-xs); }

/* ======== 消息流 ======== */
.chat-scroll { flex: 1; overflow-y: auto; padding: 28px 24px 4px; }
.msg-col {
  max-width: 736px; width: 100%; margin: 0 auto;
  display: flex; flex-direction: column; gap: 28px; padding-bottom: 16px;
}
.msg { display: flex; }
.msg.user { justify-content: flex-end; }

.user-bubble {
  max-width: 82%; padding: 11px 16px;
  background: var(--bg-tertiary);
  border-radius: 18px 18px 4px 18px;
  font-size: 15px; line-height: 1.65;
  white-space: pre-wrap; overflow-wrap: break-word;
}

.assistant-turn { width: 100%; min-width: 0; font-size: 15px; line-height: 1.7; }

/* Markdown 排版 */
.md { overflow-wrap: break-word; }
.md :deep(p) { margin: 0 0 12px; }
.md :deep(p:last-child) { margin-bottom: 0; }
.md :deep(h1), .md :deep(h2), .md :deep(h3), .md :deep(h4) {
  margin: 22px 0 10px; font-weight: 650; letter-spacing: -0.02em; line-height: 1.35;
}
.md :deep(h1) { font-size: 20px; } .md :deep(h2) { font-size: 17.5px; }
.md :deep(h3) { font-size: 16px; } .md :deep(h4) { font-size: 15px; }
.md :deep(h1:first-child), .md :deep(h2:first-child), .md :deep(h3:first-child) { margin-top: 0; }
.md :deep(ul), .md :deep(ol) { margin: 8px 0 12px; padding-left: 24px; }
.md :deep(li) { margin: 4px 0; }
.md :deep(li > p) { margin: 0; }
.md :deep(strong) { font-weight: 650; }
.md :deep(code) {
  font-family: var(--font-mono); font-size: 13px;
  background: var(--bg-tertiary); border-radius: 5px; padding: 2px 6px;
}
.md :deep(pre) {
  margin: 12px 0; padding: 14px 16px; overflow-x: auto;
  background: var(--bg-secondary); border: 1px solid var(--border);
  border-radius: var(--radius-md);
}
.md :deep(pre code) { background: none; padding: 0; font-size: 13px; line-height: 1.6; }
.md :deep(blockquote) {
  margin: 12px 0; padding: 4px 16px;
  border-left: 3px solid var(--accent-muted); color: var(--text-secondary);
}
.md :deep(table) {
  display: block; max-width: 100%; overflow-x: auto;
  margin: 12px 0; border-collapse: collapse; font-size: 13.5px;
}
.md :deep(th), .md :deep(td) { border: 1px solid var(--border-strong); padding: 6px 12px; }
.md :deep(th) { background: var(--bg-tertiary); font-weight: 600; white-space: nowrap; }
.md :deep(hr) { margin: 20px 0; border: none; border-top: 1px solid var(--border-strong); }
.md :deep(img) { max-width: 100%; border-radius: var(--radius-sm); }

/* 图片 */
.msg-images { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.msg-images img {
  max-width: 300px; max-height: 200px;
  border-radius: var(--radius-md); border: 1px solid var(--border);
  display: block;
}
.user-bubble .msg-images { margin: 2px 0 8px; }
.user-bubble .msg-images img { max-width: 240px; max-height: 160px; }

/* ======== 工具活动 ======== */
.tool-rows {
  margin-bottom: 14px;
  border: 1px solid var(--border); border-radius: var(--radius-md);
  background: var(--bg-primary); overflow: hidden;
}
.tool-row { border-top: 1px solid var(--border); }
.tool-row:first-child { border-top: none; }
.tool-head {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 14px; font-size: 13px; user-select: none;
}
.tool-head.clickable, details.tool-row > summary.tool-head { cursor: pointer; }
details.tool-row > summary.tool-head { list-style: none; }
details.tool-row > summary.tool-head::-webkit-details-marker { display: none; }
.tool-dot {
  width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0;
  background: var(--accent);
}
.tool-dot.running { animation: toolPulse 1.1s ease-in-out infinite; }
.tool-dot.done { background: var(--success); }
.tool-dot.error { background: var(--danger); }
@keyframes toolPulse {
  0%, 100% { box-shadow: 0 0 0 0 var(--accent-subtle); opacity: 1; }
  50% { box-shadow: 0 0 0 5px var(--accent-subtle); opacity: 0.55; }
}
.tool-label { font-weight: 550; flex-shrink: 0; color: var(--text-primary); }
.tool-note {
  flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-family: var(--font-mono); font-size: 11.5px; color: var(--text-tertiary);
}
.tool-ms { flex-shrink: 0; font-size: 11px; color: var(--text-tertiary); font-variant-numeric: tabular-nums; }
.chev { width: 14px; height: 14px; flex-shrink: 0; color: var(--text-tertiary); transition: transform var(--transition-fast); }
.chev.open, details[open] > summary .chev { transform: rotate(180deg); }
.tool-pre {
  margin: 0; padding: 10px 14px; max-height: 240px; overflow: auto;
  border-top: 1px dashed var(--border-strong);
  background: var(--bg-surface);
  font-family: var(--font-mono); font-size: 12px; line-height: 1.55;
  color: var(--text-secondary); white-space: pre-wrap; word-break: break-all;
}

/* 已完成轮次的工具轨迹（折叠） */
.trace { margin-bottom: 12px; }
.trace > summary {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 5px 11px; border: 1px solid var(--border); border-radius: var(--radius-sm);
  font-size: 12.5px; color: var(--text-tertiary); cursor: pointer; user-select: none;
  list-style: none; transition: all var(--transition-fast);
}
.trace > summary::-webkit-details-marker { display: none; }
.trace > summary:hover { color: var(--text-secondary); background: var(--bg-tertiary); }
.trace .t-ico { width: 13px; height: 13px; }
.trace[open] > summary { margin-bottom: 10px; }
.trace .tool-rows { margin-bottom: 0; }

/* 思考中 */
.thinking { display: flex; align-items: center; gap: 10px; font-size: 14px; color: var(--text-secondary); }
.think-mark { width: 18px; height: 18px; color: var(--accent); animation: thinkPulse 1.6s ease-in-out infinite; }
@keyframes thinkPulse {
  0%, 100% { transform: scale(1) rotate(0deg); opacity: 0.9; }
  50% { transform: scale(0.78) rotate(45deg); opacity: 0.45; }
}
.stream-cursor {
  display: inline-block; width: 7px; height: 15px; margin-top: 4px;
  background: var(--accent); border-radius: 2px;
  animation: cursorBlink 0.9s ease-in-out infinite;
}
@keyframes cursorBlink { 50% { opacity: 0.15; } }

/* 错误与操作 */
.msg-error {
  display: inline-flex; align-items: center; gap: 10px;
  width: fit-content; max-width: 100%;
  margin-top: 10px; padding: 7px 14px;
  background: var(--danger-subtle); border-radius: 999px;
  color: var(--danger); font-size: 13px;
}
.msg-error.soft { background: var(--bg-tertiary); color: var(--text-tertiary); }
.retry-btn {
  padding: 3px 12px; border: 1px solid currentColor; border-radius: 999px;
  background: none; color: inherit; font: inherit; font-size: 12px; font-weight: 600;
  cursor: pointer; transition: all var(--transition-fast);
}
.retry-btn:hover { background: var(--danger); border-color: var(--danger); color: #fff; }

.msg-actions { margin-top: 8px; opacity: 0; transition: opacity var(--transition-fast); }
.assistant-turn:hover .msg-actions { opacity: 1; }
.action-btn {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 4px 9px; border: none; border-radius: 6px;
  background: none; color: var(--text-tertiary);
  font: inherit; font-size: 12px; cursor: pointer;
  transition: all var(--transition-fast);
}
.action-btn:hover { background: var(--bg-tertiary); color: var(--text-primary); }
.action-btn svg { width: 13px; height: 13px; }

/* ======== 输入区 ======== */
.composer-zone { flex-shrink: 0; position: relative; padding: 0 24px; }
.chat-body:not(.hero) .composer-zone::before {
  content: ''; position: absolute; left: 0; right: 0; top: -30px; height: 30px;
  background: linear-gradient(to bottom, transparent, var(--bg-base));
  pointer-events: none;
}
.composer {
  max-width: 736px; margin: 0 auto;
  background: var(--bg-secondary);
  border: 1px solid var(--border-strong); border-radius: var(--radius-xl);
  box-shadow: var(--shadow-md);
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}
.composer:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-subtle), var(--shadow-md);
}
.composer textarea {
  display: block; width: 100%; max-height: 220px;
  padding: 13px 18px 4px; border: none; outline: none; resize: none;
  background: transparent; color: var(--text-primary);
  font: inherit; font-size: 15px; line-height: 1.6;
}
.composer textarea::placeholder { color: var(--text-tertiary); }
.composer-bar { display: flex; align-items: center; gap: 4px; padding: 4px 8px 8px 10px; }
.bar-space { flex: 1; }
.icon-btn {
  display: flex; align-items: center; justify-content: center;
  width: 32px; height: 32px; border: none; border-radius: var(--radius-sm);
  background: none; color: var(--text-tertiary); cursor: pointer;
  transition: all var(--transition-fast);
}
.icon-btn:hover { background: var(--bg-tertiary); color: var(--text-primary); }
.icon-btn svg { width: 17px; height: 17px; }
.send-btn {
  display: flex; align-items: center; justify-content: center;
  width: 34px; height: 34px; border: none; border-radius: 50%;
  background: var(--accent); color: #fff; cursor: pointer;
  transition: all var(--transition-fast);
}
.send-btn:hover { background: var(--accent-hover); transform: translateY(-1px); box-shadow: 0 4px 12px rgba(218, 119, 86, 0.35); }
.send-btn:disabled {
  background: var(--bg-elevated); color: var(--text-tertiary);
  cursor: default; transform: none; box-shadow: none;
}
.send-btn svg { width: 17px; height: 17px; }

.pend-imgs { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px 14px 0; }
.pend-chip { position: relative; }
.pend-chip img {
  width: 56px; height: 56px; object-fit: cover; display: block;
  border-radius: var(--radius-sm); border: 1px solid var(--border);
}
.pend-chip button {
  position: absolute; top: -6px; right: -6px;
  width: 18px; height: 18px; border-radius: 50%;
  border: 1px solid var(--border-strong); background: var(--bg-elevated);
  color: var(--text-primary); font-size: 9px; line-height: 1; cursor: pointer;
}
.composer-note {
  max-width: 736px; margin: 8px auto 0; padding-bottom: 12px;
  text-align: center; font-size: 11.5px; color: var(--text-tertiary);
}
.hero-chips + .composer-note { display: none; }

.scroll-pill {
  position: absolute; top: -48px; left: 50%; transform: translateX(-50%);
  display: flex; align-items: center; justify-content: center;
  width: 34px; height: 34px; border-radius: 50%;
  border: 1px solid var(--border-strong); background: var(--bg-secondary);
  color: var(--text-secondary); cursor: pointer; box-shadow: var(--shadow-md);
  transition: all var(--transition-fast); z-index: 2;
}
.scroll-pill:hover { color: var(--text-primary); transform: translate(-50%, -2px); }
.scroll-pill svg { width: 16px; height: 16px; }

/* 焦点可达性 */
.new-chat:focus-visible, .chip:focus-visible, .send-btn:focus-visible,
.icon-btn:focus-visible, .action-btn:focus-visible, .scroll-pill:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px;
}

/* 减弱动效 */
@media (prefers-reduced-motion: reduce) {
  .hero-mark, .think-mark, .tool-dot.running, .stream-cursor { animation: none; }
  .composer, .sess-item, .chip, .send-btn { transition: none; }
}
</style>

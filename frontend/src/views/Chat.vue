<!-- frontend/src/views/Chat.vue -->
<template>
  <div class="chat-page">
    <aside class="chat-side">
      <button class="btn primary new-btn" @click="newSession">＋ 新会话</button>
      <div class="sess-list">
        <div v-for="s in sessions" :key="s.id" class="sess-item"
             :class="{ active: s.id === activeId }" @click="selectSession(s.id)">
          <span class="sess-title">{{ s.title }}</span>
          <span class="sess-ops">
            <button title="重命名" @click.stop="renameSession(s)">✎</button>
            <button title="删除" @click.stop="removeSession(s)">✕</button>
          </span>
        </div>
      </div>
    </aside>

    <section class="chat-main">
      <div v-if="!activeId" class="chat-empty">选择左侧会话，或新建一个开始对话</div>
      <template v-else>
        <div ref="scrollEl" class="chat-scroll">
          <div v-for="m in messages" :key="m.id" class="msg" :class="m.role">
            <div class="bubble">
              <div v-if="m.images && m.images.length" class="msg-images">
                <a v-for="img in m.images" :key="img.filename"
                   :href="api.chatImageUrl(img.kind, img.filename)" target="_blank">
                  <img :src="api.chatImageUrl(img.kind, img.filename)" />
                </a>
              </div>
              <div v-if="m.role === 'assistant'" class="md" v-html="renderMd(m.content)"></div>
              <div v-else class="plain">{{ m.content }}</div>
              <details v-if="traceSteps(m).length" class="trace">
                <summary>工具轨迹（{{ traceSteps(m).length }} 次调用）</summary>
                <div v-for="(st, i) in traceSteps(m)" :key="i" class="trace-step">
                  <code>{{ st.tool }}</code> {{ shortJson(st.args) }}
                  <pre>{{ st.result }}</pre>
                </div>
              </details>
              <div v-if="m.error" class="msg-error">
                {{ m.error === 'cancelled' ? '已停止' : '出错：' + m.error }}
                <button v-if="retryTargetOf(m)" class="btn tiny" @click="retry(m)">重试</button>
              </div>
            </div>
          </div>

          <div v-if="live.active" class="msg assistant">
            <div class="bubble">
              <div v-for="(c, i) in live.cards" :key="i" class="tool-card" :class="c.status">
                <div class="tool-head">
                  <span class="tool-dot" :class="c.status"></span>
                  <code>{{ c.tool }}</code>
                  <span class="tool-note">{{ c.note }}</span>
                  <span v-if="c.elapsed" class="tool-ms">{{ c.elapsed }}ms</span>
                </div>
                <details v-if="c.summary"><summary>结果摘要</summary><pre>{{ c.summary }}</pre></details>
              </div>
              <div v-if="live.images.length" class="msg-images">
                <a v-for="img in live.images" :key="img.filename"
                   :href="api.chatImageUrl(img.kind, img.filename)" target="_blank">
                  <img :src="api.chatImageUrl(img.kind, img.filename)" />
                </a>
              </div>
              <div class="md" v-html="renderMd(live.text)"></div>
              <span class="cursor">▍</span>
            </div>
          </div>
        </div>

        <div class="chat-input-wrap">
          <div v-if="pendingFiles.length" class="pend-imgs">
            <span v-for="(f, i) in pendingFiles" :key="i" class="pend-chip">
              <img :src="f.preview" />
              <button @click="pendingFiles.splice(i, 1)">✕</button>
            </span>
          </div>
          <div class="chat-input">
            <button class="btn ghost" title="添加图片" @click="fileInput.click()">🖼</button>
            <input ref="fileInput" type="file" accept="image/png,image/jpeg" multiple
                   style="display:none" @change="pickFiles" />
            <textarea v-model="draft" rows="2" :disabled="live.active"
                      placeholder="问点什么…（Enter 发送，Shift+Enter 换行，可粘贴图片）"
                      @keydown.enter.exact="onEnterKey" @paste="onPaste"></textarea>
            <button v-if="live.active" class="btn danger" @click="stop">■ 停止</button>
            <button v-else class="btn primary"
                    :disabled="!draft.trim() && !pendingFiles.length" @click="send">发送</button>
          </div>
        </div>
      </template>
    </section>
  </div>
</template>

<script setup>
import { ref, reactive, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { api } from '../api/client.js'

const sessions = ref([])
const activeId = ref(null)
const messages = ref([])
const draft = ref('')
const scrollEl = ref(null)
const pendingFiles = ref([])
const fileInput = ref(null)
const live = reactive({ active: false, turnId: null, text: '', cards: [], images: [] })

let es = null
let reconnectTimer = null
let lastEventId = 0
let reconnectDelay = 1000

function renderMd(text) {
  return DOMPurify.sanitize(marked.parse(text || ''))
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

async function scrollBottom() {
  await nextTick()
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight
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

function onEnterKey(e) {
  // 输入法组词中：Enter 只确认候选词，不发送（isComposing 标准；229 兼容旧 IME 事件）
  if (e.isComposing || e.keyCode === 229) return
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
  if (type === 'text_delta') { live.active = true; live.text += p.text }
  else if (type === 'tool_start') {
    live.active = true
    live.cards.push({ tool: p.tool, status: 'running', note: shortJson(p.args), summary: '', elapsed: 0 })
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
  activeId.value = id
  resetLive()
  const data = await api.getChatMessages(id)
  messages.value = data.messages
  lastEventId = data.last_event_id
  if (data.active_turn) {
    // 恢复进行中轮次：先回放已有事件，再从 last_event_id 跟播
    live.active = true
    live.turnId = data.active_turn.id
    for (const ev of data.active_events) replayEvent(ev.type, ev.payload)
  }
  openStream()
  scrollBottom()
}

async function newSession() {
  const { id } = await api.createChatSession()
  await loadSessions()
  await selectSession(id)
}

async function renameSession(s) {
  const t = prompt('会话标题', s.title)
  if (t && t.trim()) { await api.renameChatSession(s.id, t.trim()); await loadSessions() }
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
  const files = pendingFiles.value.map(p => p.file)
  if ((!text && !files.length) || live.active) return
  draft.value = ''
  pendingFiles.value = []
  try {
    const r = await api.sendChatMessage(activeId.value, text, files)
    const data = await api.getChatMessages(activeId.value)   // 拿服务端落库的 images 引用
    messages.value = data.messages
    live.active = true
    live.turnId = r.turn_id
    scrollBottom()
  } catch (e) {
    alert('发送失败：' + e.message)
    draft.value = text
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
onBeforeUnmount(closeStream)
</script>

<style scoped>
.chat-page { display: flex; height: calc(100vh - 80px); gap: 12px; }
.chat-side { width: 220px; flex-shrink: 0; display: flex; flex-direction: column; gap: 8px; }
.new-btn { width: 100%; }
.sess-list { overflow-y: auto; flex: 1; }
.sess-item { display: flex; justify-content: space-between; align-items: center;
  padding: 8px 10px; border-radius: var(--radius-sm); cursor: pointer; font-size: 13px; }
.sess-item:hover { background: var(--bg-tertiary); }
.sess-item.active { background: var(--accent-subtle); color: var(--accent); }
.sess-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sess-ops { visibility: hidden; }
.sess-item:hover .sess-ops { visibility: visible; }
.sess-ops button { background: none; border: none; cursor: pointer; opacity: .6; color: inherit; }
.chat-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.chat-empty { margin: auto; color: var(--text-secondary); }
.chat-scroll { flex: 1; overflow-y: auto; padding: 8px 16px; }
.msg { display: flex; margin-bottom: 12px; }
.msg.user { justify-content: flex-end; }
.bubble { max-width: 78%; padding: 10px 14px; border-radius: var(--radius-md);
  background: var(--bg-secondary); overflow-wrap: break-word; }
.msg.user .bubble { background: var(--accent-subtle); white-space: pre-wrap; }
.md :deep(pre) { overflow-x: auto; padding: 8px; border-radius: var(--radius-xs);
  background: var(--bg-elevated); }
.md :deep(table) { border-collapse: collapse; }
.md :deep(td), .md :deep(th) { border: 1px solid var(--border-strong); padding: 2px 8px; }
.msg-images img { max-width: 260px; max-height: 180px; border-radius: var(--radius-sm); margin: 4px 6px 4px 0; }
.tool-card { border: 1px solid var(--border-strong); border-radius: var(--radius-sm);
  padding: 6px 10px; margin-bottom: 6px; font-size: 12.5px; }
.tool-card.error { border-color: var(--danger); }
.tool-head { display: flex; align-items: center; gap: 8px; }
.tool-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--warning); flex-shrink: 0; }
.tool-dot.running { animation: pulse 1s infinite; }
.tool-dot.done { background: var(--success); }
.tool-dot.error { background: var(--danger); }
@keyframes pulse { 50% { opacity: .3; } }
.tool-note { opacity: .75; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tool-ms { margin-left: auto; opacity: .5; }
.tool-card pre, .trace pre { max-height: 200px; overflow: auto; font-size: 11.5px;
  white-space: pre-wrap; word-break: break-all; }
.trace { margin-top: 8px; font-size: 12px; opacity: .85; }
.trace-step { margin: 6px 0; }
.msg-error { color: var(--danger); font-size: 12.5px; margin-top: 6px; }
.cursor { animation: pulse .8s infinite; }
.chat-input { display: flex; gap: 8px; padding: 10px 16px; align-items: flex-end; }
.chat-input textarea { flex: 1; resize: none; border-radius: var(--radius-md); padding: 10px;
  background: var(--bg-primary); border: 1px solid var(--border-strong);
  color: inherit; font: inherit; }
.btn { padding: 8px 18px; border: none; cursor: pointer; border-radius: var(--radius-sm, 8px); }
.btn.primary { background: var(--accent); color: #fff; }
.btn.danger { background: var(--danger); color: #fff; }
.btn.tiny { padding: 2px 8px; font-size: 12px; margin-left: 8px; }
.btn:disabled { opacity: .4; cursor: default; }
.pend-imgs { display: flex; gap: 8px; padding: 0 16px; }
.pend-chip { position: relative; }
.pend-chip img { width: 56px; height: 56px; object-fit: cover; border-radius: 8px; }
.pend-chip button { position: absolute; top: -6px; right: -6px; border-radius: 50%;
  border: none; width: 18px; height: 18px; font-size: 10px; cursor: pointer; }
.btn.ghost { background: none; border: 1px solid rgba(128,128,128,.3); }
</style>

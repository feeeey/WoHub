const BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  })

  if (res.status === 401 && !path.startsWith('/auth/')) {
    window.location.href = '/login'
    throw new Error('Unauthorized')
  }

  if (!res.ok) {
    let detail = `${res.status}`
    try {
      const body = await res.json()
      if (body.detail) detail = body.detail
    } catch {}
    throw new Error(detail)
  }

  return res.json()
}

export const api = {
  async login(password) {
    const form = new URLSearchParams()
    form.append('password', password)
    const res = await fetch(`${BASE}/auth/login`, {
      method: 'POST',
      body: form,
    })
    if (!res.ok) throw new Error('Login failed')
    return res.json()
  },

  async logout() {
    return request('/auth/logout', { method: 'POST' })
  },

  async authStatus() {
    return request('/auth/status')
  },

  async health() {
    return request('/health')
  },

  async fundingRates() {
    return request('/market/funding-rates')
  },

  async gainers() {
    return request('/market/gainers')
  },

  async losers() {
    return request('/market/losers')
  },

  async compare(symbol) {
    return request(`/market/compare/${encodeURIComponent(symbol)}`)
  },

  async exportList(exchange = 'all') {
    const res = await fetch(`${BASE}/market/export?exchange=${exchange}`)
    return res.text()
  },

  async listChannels() {
    return request('/channels')
  },

  async createChannel(data) {
    return request('/channels', { method: 'POST', body: JSON.stringify(data) })
  },

  async updateChannel(id, data) {
    return request(`/channels/${id}`, { method: 'PUT', body: JSON.stringify(data) })
  },

  async deleteChannel(id) {
    return request(`/channels/${id}`, { method: 'DELETE' })
  },

  async testChannel(id) {
    return request(`/channels/${id}/test`, { method: 'POST' })
  },

  async getChannelHistory(id) {
    return request(`/channels/${id}/history`)
  },

  async listTasks() {
    return request('/tasks')
  },

  async createTask(data) {
    return request('/tasks', { method: 'POST', body: JSON.stringify(data) })
  },

  async updateTask(id, data) {
    return request(`/tasks/${id}`, { method: 'PUT', body: JSON.stringify(data) })
  },

  async deleteTask(id) {
    return request(`/tasks/${id}`, { method: 'DELETE' })
  },

  async startTask(id) {
    return request(`/tasks/${id}/start`, { method: 'POST' })
  },

  async stopTask(id) {
    return request(`/tasks/${id}/stop`, { method: 'POST' })
  },

  async testTask(id) {
    return request(`/tasks/${id}/test`, { method: 'POST' })
  },

  async getScreeners() {
    return request('/tasks/screeners')
  },

  async getTaskHistory(id) {
    return request(`/tasks/${id}/history`)
  },

  async getWatchlists() {
    return request('/tasks/watchlists')
  },

  async runScan(data) {
    return request('/scanner/run', { method: 'POST', body: JSON.stringify(data) })
  },

  async getSettings() {
    return request('/settings/info')
  },

  async getCookies() {
    return request('/settings/cookies')
  },

  async updateCookies(raw) {
    return request('/settings/cookies', { method: 'PUT', body: JSON.stringify({ cookies: raw }) })
  },

  async getLogs(source, level, limit = 100) {
    let url = `/settings/logs?limit=${limit}`
    if (source) url += `&source=${source}`
    if (level) url += `&level=${level}`
    return request(url)
  },

  async clearLogs() {
    return request('/settings/logs', { method: 'DELETE' })
  },

  async getProxy() {
    return request('/settings/proxy')
  },

  async updateProxy(data) {
    return request('/settings/proxy', { method: 'PUT', body: JSON.stringify(data) })
  },

  async getChartshotStatus() {
    return request('/screenshots/status')
  },

  async getChartshotCookies() {
    return request('/screenshots/cookies')
  },

  async updateChartshotCookies(raw) {
    return request('/screenshots/cookies', { method: 'PUT', body: JSON.stringify({ cookies: raw }) })
  },

  async testChartshotCookies() {
    return request('/screenshots/cookies/test', { method: 'POST' })
  },

  async getKlines(symbol, interval, limit = 100, includeCurrent = false) {
    const params = new URLSearchParams({
      symbol,
      interval,
      limit: String(limit),
      include_current: String(includeCurrent),
    })
    return request(`/klines/binance?${params}`)
  },

  // ---- trading ----

  async listTradingCredentials() {
    return request('/trading/credentials')
  },

  async addTradingCredential(data) {
    return request('/trading/credentials', { method: 'POST', body: JSON.stringify(data) })
  },

  async deleteTradingCredential(id) {
    return request(`/trading/credentials/${id}`, { method: 'DELETE' })
  },

  async toggleTradingCredential(id, enabled) {
    return request(`/trading/credentials/${id}/enabled`, {
      method: 'POST', body: JSON.stringify({ enabled }),
    })
  },

  async testTradingCredential(id) {
    return request(`/trading/credentials/${id}/test`, { method: 'POST' })
  },

  async getTradingAccount(credentialId) {
    return request(`/trading/account/${credentialId}`)
  },

  async placeTradingOrder(data) {
    return request('/trading/order', { method: 'POST', body: JSON.stringify(data) })
  },

  async getTradingOrders(limit = 50) {
    return request(`/trading/orders?limit=${limit}`)
  },

  async placeBracketOrder(data) {
    return request('/trading/order/bracket', { method: 'POST', body: JSON.stringify(data) })
  },

  async closeTradingPosition(credentialId, symbol) {
    return request('/trading/position/close', {
      method: 'POST',
      body: JSON.stringify({ credential_id: credentialId, symbol }),
    })
  },

  async killSwitch(credentialId) {
    return request('/trading/kill-switch', {
      method: 'POST',
      body: JSON.stringify({ credential_id: credentialId }),
    })
  },

  async getOpenOrders(credentialId, symbol = null) {
    const q = symbol ? `?symbol=${encodeURIComponent(symbol)}` : ''
    return request(`/trading/open-orders/${credentialId}${q}`)
  },

  async cancelOpenOrder(credentialId, symbol, orderId) {
    return request('/trading/open-orders/cancel', {
      method: 'POST',
      body: JSON.stringify({ credential_id: credentialId, symbol, order_id: orderId }),
    })
  },

  async getBinanceOrderHistory(credentialId, symbol, limit = 50) {
    return request(`/trading/history/${credentialId}?symbol=${encodeURIComponent(symbol)}&limit=${limit}`)
  },

  async buildTradingPlan(data) {
    return request('/trading/plan', { method: 'POST', body: JSON.stringify(data) })
  },

  // ---- agent ----

  async getAgentConfig() {
    return request('/agent/config')
  },

  async updateAgentConfig(data) {
    return request('/agent/config', { method: 'PUT', body: JSON.stringify(data) })
  },

  // ---- chat ----

  async listChatSessions() {
    return request('/chat/sessions')
  },

  async createChatSession(title = null) {
    return request('/chat/sessions', { method: 'POST', body: JSON.stringify({ title }) })
  },

  async renameChatSession(id, title) {
    return request(`/chat/sessions/${id}`, { method: 'PATCH', body: JSON.stringify({ title }) })
  },

  async deleteChatSession(id) {
    return request(`/chat/sessions/${id}`, { method: 'DELETE' })
  },

  async getChatMessages(id) {
    return request(`/chat/sessions/${id}/messages`)
  },

  async sendChatMessage(id, content, files = []) {
    const form = new FormData()
    form.append('content', content)
    for (const f of files) form.append('files', f)
    const res = await fetch(`${BASE}/chat/sessions/${id}/messages`, {
      method: 'POST',
      body: form,
    })
    if (res.status === 401) { window.location.href = '/login'; throw new Error('Unauthorized') }
    if (!res.ok) {
      let detail = `${res.status}`
      try { detail = (await res.json()).detail || detail } catch {}
      throw new Error(detail)
    }
    return res.json()
  },

  async cancelChatTurn(turnId) {
    return request(`/chat/turns/${turnId}/cancel`, { method: 'POST' })
  },

  async retryChatMessage(messageId) {
    return request(`/chat/messages/${messageId}/retry`, { method: 'POST' })
  },

  chatStreamUrl(sessionId, after = 0) {
    return `${BASE}/chat/sessions/${sessionId}/stream?after=${after}`
  },

  chatImageUrl(kind, filename) {
    return `${BASE}/chat/images/${kind}/${encodeURIComponent(filename)}`
  },

  // ---- agent v2 (model list / connectivity test / screener semantics) ----

  async fetchAgentModels(overrides = {}) {
    return request('/agent/models', { method: 'POST', body: JSON.stringify(overrides) })
  },

  async testAgentLlm(overrides = {}) {
    return request('/agent/test', { method: 'POST', body: JSON.stringify(overrides) })
  },

  async getScreenerSemantics() {
    return request('/agent/semantics')
  },

  async saveScreenerSemantics(key, fields) {
    return request(`/agent/semantics/${key}`, { method: 'PUT', body: JSON.stringify(fields) })
  },
}

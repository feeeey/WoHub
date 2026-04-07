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
    throw new Error(`API error: ${res.status}`)
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

  async getSettings() {
    return request('/settings/info')
  },

  async getCookies() {
    return request('/settings/cookies')
  },

  async updateCookies(raw) {
    return request('/settings/cookies', { method: 'PUT', body: JSON.stringify({ cookies: raw }) })
  },

  async getProxy() {
    return request('/settings/proxy')
  },

  async updateProxy(data) {
    return request('/settings/proxy', { method: 'PUT', body: JSON.stringify(data) })
  },

  async getChartshotStatus() {
    return request('/settings/chartshot/status')
  },

  async getChartshotCookies() {
    return request('/settings/chartshot/cookies')
  },

  async updateChartshotCookies(raw) {
    return request('/settings/chartshot/cookies', { method: 'PUT', body: JSON.stringify({ cookies: raw }) })
  },

  async testChartshotCookies() {
    return request('/settings/chartshot/cookies/test', { method: 'POST' })
  },

  async getAIConfig() {
    return request('/ai/config')
  },

  async updateAIConfig(data) {
    return request('/ai/config', { method: 'PUT', body: JSON.stringify(data) })
  },

  async testAIConnection() {
    return request('/ai/test', { method: 'POST' })
  },

  async getAISignals() {
    return request('/ai/signals')
  },

  async getSignalDetail(id) {
    return request(`/ai/signals/${id}`)
  },

  async listStrategies() {
    return request('/ai/strategies')
  },

  async createStrategy(data) {
    return request('/ai/strategies', { method: 'POST', body: JSON.stringify(data) })
  },

  async updateStrategy(id, data) {
    return request(`/ai/strategies/${id}`, { method: 'PUT', body: JSON.stringify(data) })
  },

  async deleteStrategy(id) {
    return request(`/ai/strategies/${id}`, { method: 'DELETE' })
  },

  async setDefaultStrategy(id) {
    return request(`/ai/strategies/${id}/default`, { method: 'POST' })
  },
}

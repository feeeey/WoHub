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
}

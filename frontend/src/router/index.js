import { createRouter, createWebHistory } from 'vue-router'
import Login from '../views/Login.vue'
import Tasks from '../views/Tasks.vue'
import Market from '../views/Market.vue'
import Agent from '../views/Agent.vue'
import Channels from '../views/Channels.vue'
import Settings from '../views/Settings.vue'
import Scanner from '../views/Scanner.vue'
import Trade from '../views/Trade.vue'

const routes = [
  { path: '/login', component: Login, meta: { public: true } },
  { path: '/', redirect: '/tasks' },
  { path: '/tasks', component: Tasks },
  { path: '/scanner', component: Scanner },
  { path: '/market', component: Market },
  { path: '/trade', component: Trade },
  // backward-compat alias: K线形态 used to live at /klines
  { path: '/klines', redirect: '/trade' },
  { path: '/agent', component: Agent },
  { path: '/channels', component: Channels },
  { path: '/settings', component: Settings },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  if (to.meta.public) return true

  try {
    const res = await fetch('/api/auth/status')
    const data = await res.json()
    if (!data.authenticated) return '/login'
  } catch {
    return '/login'
  }
})

export default router

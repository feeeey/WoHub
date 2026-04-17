import { createRouter, createWebHistory } from 'vue-router'
import Login from '../views/Login.vue'
import Tasks from '../views/Tasks.vue'
import Market from '../views/Market.vue'
import Channels from '../views/Channels.vue'
import Settings from '../views/Settings.vue'
import AI from '../views/AI.vue'
import Scanner from '../views/Scanner.vue'

const routes = [
  { path: '/login', component: Login, meta: { public: true } },
  { path: '/', redirect: '/tasks' },
  { path: '/tasks', component: Tasks },
  { path: '/scanner', component: Scanner },
  { path: '/market', component: Market },
  { path: '/channels', component: Channels },
  { path: '/settings', component: Settings },
  { path: '/ai', component: AI },
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

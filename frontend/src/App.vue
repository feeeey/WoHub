<template>
  <div v-if="route.meta.public">
    <router-view />
  </div>
  <div v-else class="app-layout">
    <aside class="sidebar">
      <div class="sidebar-header">WoHub</div>
      <nav class="sidebar-nav">
        <router-link
          v-for="item in navItems"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          :class="{ active: route.path === item.path }"
        >
          <span>{{ item.icon }}</span>
          <span>{{ item.label }}</span>
        </router-link>
      </nav>
      <div class="nav-item" @click="handleLogout" style="border-top: 1px solid var(--border)">
        <span>Exit</span>
      </div>
    </aside>
    <main class="main-content">
      <router-view />
    </main>
  </div>
</template>

<script setup>
import { useRoute, useRouter } from 'vue-router'
import { api } from './api/client.js'

const route = useRoute()
const router = useRouter()

const navItems = [
  { path: '/tasks', icon: '\u{1F4CB}', label: 'Tasks' },
  { path: '/market', icon: '\u{1F4CA}', label: 'Market' },
  { path: '/channels', icon: '\u{1F4E1}', label: 'Channels' },
  { path: '/settings', icon: '\u2699\uFE0F', label: 'Settings' },
]

async function handleLogout() {
  await api.logout()
  router.push('/login')
}
</script>

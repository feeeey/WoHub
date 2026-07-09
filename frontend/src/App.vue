<template>
  <div v-if="route.meta.public">
    <router-view />
  </div>
  <div v-else class="app-layout">
    <aside class="sidebar">
      <div class="sidebar-header">
        <div class="logo">W</div>
        <span class="brand">WoHub</span>
      </div>
      <nav class="sidebar-nav">
        <router-link
          v-for="item in navItems"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          :class="{ active: route.path === item.path }"
        >
          <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" v-html="item.icon"></svg>
          <span>{{ item.label }}</span>
        </router-link>
      </nav>
      <div class="sidebar-footer">
        <div class="theme-toggle" @click="toggleTheme">
          <svg v-if="isDark" class="toggle-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="5" />
            <line x1="12" y1="1" x2="12" y2="3" />
            <line x1="12" y1="21" x2="12" y2="23" />
            <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
            <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
            <line x1="1" y1="12" x2="3" y2="12" />
            <line x1="21" y1="12" x2="23" y2="12" />
            <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
            <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
          </svg>
          <svg v-else class="toggle-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
          </svg>
          <span>{{ isDark ? '浅色模式' : '深色模式' }}</span>
        </div>
        <div class="nav-item" @click="handleLogout">
          <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
            <polyline points="16 17 21 12 16 7" />
            <line x1="21" y1="12" x2="9" y2="12" />
          </svg>
          <span>退出登录</span>
        </div>
      </div>
    </aside>
    <main class="main-content" :class="{ 'full-bleed': route.meta.fullBleed }">
      <router-view v-slot="{ Component }">
        <transition name="route" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from './api/client.js'

const route = useRoute()
const router = useRouter()

const isDark = ref(true)

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme)
  isDark.value = theme === 'dark'
}

function toggleTheme() {
  const next = isDark.value ? 'light' : 'dark'
  localStorage.setItem('wohub-theme', next)
  applyTheme(next)
}

onMounted(() => {
  const saved = localStorage.getItem('wohub-theme') || 'dark'
  applyTheme(saved)
})

const navItems = [
  {
    path: '/tasks',
    label: '定时任务',
    icon: '<rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" />',
  },
  {
    path: '/scanner',
    label: '筛选器',
    icon: '<circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />',
  },
  {
    path: '/market',
    label: '市场看板',
    icon: '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />',
  },
  {
    path: '/trade',
    label: '交易终端',
    icon: '<line x1="6" y1="3" x2="6" y2="21" /><rect x="4" y="7" width="4" height="9" /><line x1="14" y1="3" x2="14" y2="21" /><rect x="12" y="11" width="4" height="6" /><line x1="20" y1="5" x2="20" y2="19" /><rect x="18" y="8" width="4" height="8" />',
  },
  {
    path: '/agent',
    label: 'Agent 对话',
    icon: '<rect x="3" y="4" width="18" height="14" rx="2" /><circle cx="9" cy="10" r="1.5" /><circle cx="15" cy="10" r="1.5" /><path d="M9 14h6" /><line x1="8" y1="1" x2="8" y2="4" /><line x1="16" y1="1" x2="16" y2="4" />',
  },
  {
    path: '/channels',
    label: '推送通道',
    icon: '<path d="M22 2L11 13" /><path d="M22 2L15 22L11 13L2 9L22 2Z" />',
  },
  {
    path: '/settings',
    label: '系统设置',
    icon: '<circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />',
  },
]

async function handleLogout() {
  await api.logout()
  router.push('/login')
}
</script>

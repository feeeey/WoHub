import { createApp } from 'vue'
import App from './App.vue'
import router from './router/index.js'
import './assets/style.css'

// Apply saved theme before mount to prevent flash
document.documentElement.setAttribute(
  'data-theme',
  localStorage.getItem('wohub-theme') || 'dark'
)

const app = createApp(App)
app.use(router)
app.mount('#app')

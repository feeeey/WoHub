<template>
  <div class="login-container">
    <div class="login-ambient">
      <div class="orb orb-1"></div>
      <div class="orb orb-2"></div>
    </div>
    <div class="login-card">
      <div class="login-brand">
        <div class="login-logo">W</div>
        <h1>WoHub</h1>
        <p>信号监控与推送管理平台</p>
      </div>
      <form @submit.prevent="handleLogin">
        <div class="form-group">
          <label>密码</label>
          <input type="password" v-model="password" placeholder="请输入密码" autofocus />
        </div>
        <button class="btn btn-primary" type="submit">登录</button>
        <p v-if="error" class="login-error">{{ error }}</p>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client.js'

const router = useRouter()
const password = ref('')
const error = ref('')

async function handleLogin() {
  error.value = ''
  try {
    await api.login(password.value)
    router.push('/tasks')
  } catch (e) {
    error.value = '密码错误'
  }
}
</script>

<template>
  <div class="login-container">
    <div class="login-box">
      <h1>WoHub</h1>
      <form @submit.prevent="handleLogin">
        <div class="form-group">
          <label>Password</label>
          <input type="password" v-model="password" placeholder="Enter password" autofocus />
        </div>
        <button class="btn btn-primary" type="submit">Login</button>
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
    error.value = 'Wrong password'
  }
}
</script>

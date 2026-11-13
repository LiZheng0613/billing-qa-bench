import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api'
import { ApiError, getToken, setToken } from '../api/client'

const USER_KEY = 'billing_qa_user'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(getToken())
  const username = ref<string>(localStorage.getItem(USER_KEY) ?? '')
  const loading = ref(false)
  const error = ref<string | null>(null)

  const isAuthenticated = computed(() => Boolean(token.value))

  async function login(name: string, password: string): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const result = await api.login(name, password)
      setToken(result.access_token)
      token.value = result.access_token
      username.value = result.username
      localStorage.setItem(USER_KEY, result.username)
    } catch (exc) {
      error.value = exc instanceof ApiError ? exc.message : '登录失败'
      throw exc
    } finally {
      loading.value = false
    }
  }

  async function fetchProfile(): Promise<void> {
    if (!token.value) return
    try {
      const profile = await api.me()
      username.value = profile.username
      localStorage.setItem(USER_KEY, profile.username)
    } catch (exc) {
      if (exc instanceof ApiError && exc.status === 401) logout()
    }
  }

  function logout(): void {
    setToken(null)
    localStorage.removeItem(USER_KEY)
    token.value = null
    username.value = ''
  }

  return { token, username, loading, error, isAuthenticated, login, fetchProfile, logout }
})

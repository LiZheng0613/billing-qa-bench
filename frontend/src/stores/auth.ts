import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api'
import { ApiError, getToken, setToken } from '../api/client'
import type { UserProfile } from '../types'

const USER_KEY = 'billing_qa_user'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(getToken())
  const user = ref<UserProfile | null>(null)
  const username = ref<string>(localStorage.getItem(USER_KEY) ?? '')
  const loading = ref(false)
  const loaded = ref(false)
  const error = ref<string | null>(null)

  const isAuthenticated = computed(() => Boolean(token.value))
  const displayName = computed(() => user.value?.display_name || username.value || '未登录')
  const roles = computed(() => user.value?.roles ?? [])
  const permissions = computed(() => new Set(user.value?.permissions ?? []))
  const isAdmin = computed(() => roles.value.includes('SYSTEM_ADMIN'))
  const mustChangePassword = computed(() => Boolean(user.value?.must_change_password))

  function hasRole(role: string): boolean {
    return roles.value.includes(role)
  }

  function can(code: string): boolean {
    return permissions.value.has(code)
  }

  function canAny(codes: string[]): boolean {
    return codes.some(can)
  }

  async function login(name: string, password: string): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const result = await api.login(name, password)
      setToken(result.access_token)
      token.value = result.access_token
      user.value = result.user
      username.value = result.user.username
      loaded.value = true
      localStorage.setItem(USER_KEY, result.user.username)
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
      user.value = {
        id: profile.id,
        username: profile.username,
        display_name: profile.display_name,
        roles: profile.roles,
        permissions: profile.permissions,
        must_change_password: profile.must_change_password,
      }
      username.value = profile.username
      localStorage.setItem(USER_KEY, profile.username)
      loaded.value = true
    } catch (exc) {
      if (exc instanceof ApiError && exc.status === 401) logout()
      throw exc
    }
  }

  function applyProfile(profile: UserProfile): void {
    user.value = profile
    username.value = profile.username
    loaded.value = true
    localStorage.setItem(USER_KEY, profile.username)
  }

  function logout(): void {
    setToken(null)
    localStorage.removeItem(USER_KEY)
    token.value = null
    user.value = null
    username.value = ''
    loaded.value = false
  }

  return {
    token,
    user,
    username,
    displayName,
    roles,
    permissions,
    isAdmin,
    mustChangePassword,
    loading,
    loaded,
    error,
    isAuthenticated,
    hasRole,
    can,
    canAny,
    login,
    fetchProfile,
    applyProfile,
    logout,
  }
})

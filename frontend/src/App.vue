<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import ConfirmDialog from './components/ConfirmDialog.vue'
import DebugBubbleHost from './components/DebugBubbleHost.vue'
import ToastHost from './components/ToastHost.vue'
import { useAuthStore } from './stores/auth'
import { useToastStore } from './stores/toast'

const router = useRouter()
const auth = useAuthStore()
const toast = useToastStore()

function handleExpired(): void {
  auth.logout()
  toast.error('登录状态已失效，请重新登录')
  void router.push({ name: 'login' })
}

onMounted(() => {
  window.addEventListener('auth:expired', handleExpired)
  // 与首个路由守卫的 fetchProfile 并发触发：实际去重由 store 内的
  // 在途 Promise 复用保证（同一时刻只发一个 /auth/me）。
  if (auth.token && !auth.loaded) void auth.fetchProfile()
})

onBeforeUnmount(() => {
  window.removeEventListener('auth:expired', handleExpired)
})
</script>

<template>
  <RouterView />
  <ToastHost />
  <DebugBubbleHost />
  <ConfirmDialog />
</template>

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
  if (auth.token) void auth.fetchProfile()
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

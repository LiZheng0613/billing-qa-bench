<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()

const roles = computed(() => (auth.roles.length ? auth.roles.join(' / ') : '未加载'))

function relogin(): void {
  auth.logout()
  void router.push({ name: 'login' })
}
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-canvas px-4">
    <div class="card w-full max-w-md p-8 text-center">
      <div class="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-amber-50 text-amber-600">
        <svg class="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
        </svg>
      </div>
      <p class="eyebrow mt-4">403 Forbidden</p>
      <h1 class="mt-2 text-xl font-semibold text-ink">没有访问权限</h1>
      <p class="mt-2 text-xs leading-relaxed text-slate-500">
        当前账号没有进入该页面的权限。如果刚切换过账号，可能是浏览器里还保留着旧登录状态。
      </p>
      <div class="mt-4 rounded-xl border border-line bg-slate-50 px-3 py-2 text-left text-[10px] text-slate-500">
        <p>当前账号：<span class="font-semibold text-slate-700">{{ auth.displayName }}</span></p>
        <p class="mt-1">角色：<span class="font-mono text-slate-600">{{ roles }}</span></p>
        <p v-if="auth.mustChangePassword" class="mt-1 text-amber-700">该账号仍需完成首次改密。</p>
      </div>
      <div class="mt-6 flex flex-wrap justify-center gap-2">
        <button class="btn" @click="router.back()">返回上一页</button>
        <button class="btn" @click="router.push('/')">回到工作台</button>
        <button class="btn-primary" @click="relogin">退出并重新登录</button>
      </div>
    </div>
  </div>
</template>

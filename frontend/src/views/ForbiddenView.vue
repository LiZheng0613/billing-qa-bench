<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
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
        当前账号没有进入该页面的权限。如需开通，请联系系统管理员。
      </p>
      <div class="mt-6 flex justify-center gap-2">
        <button class="btn" @click="router.back()">返回上一页</button>
        <button class="btn-primary" @click="router.push('/')">回到工作台</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const username = ref('admin')
const password = ref('admin123')
const localError = ref<string | null>(null)

async function submit(): Promise<void> {
  localError.value = null
  if (!username.value.trim() || !password.value) {
    localError.value = '请输入用户名与密码'
    return
  }
  try {
    await auth.login(username.value.trim(), password.value)
    const target = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.replace(target)
  } catch (exc) {
    localError.value = exc instanceof ApiError ? exc.message : '登录失败，请稍后重试'
  }
}
</script>

<template>
  <div class="relative flex min-h-screen items-center justify-center overflow-hidden px-4">
    <div class="pointer-events-none absolute -left-40 top-0 h-96 w-96 rounded-full bg-indigo-600/20 blur-3xl" />
    <div class="pointer-events-none absolute -right-32 bottom-0 h-96 w-96 rounded-full bg-cyan-500/10 blur-3xl" />

    <div class="relative grid w-full max-w-4xl gap-8 lg:grid-cols-[1.1fr_1fr] lg:items-center">
      <div class="hidden lg:block">
        <div
          class="mb-5 inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-500 to-cyan-400 text-lg font-bold text-white shadow-xl shadow-indigo-500/30"
        >
          问
        </div>
        <h1 class="text-3xl font-semibold leading-tight text-white">
          问账智能客服<br />批量测试平台
        </h1>
        <p class="mt-4 max-w-md text-sm leading-relaxed text-slate-400">
          面向账单问答场景的自动化回归工作台：版本快照绑定、静态/动态多轮模拟、三重熔断、
          多裁判混合裁决与全链路 Trace 追踪。
        </p>
        <ul class="mt-6 space-y-2 text-xs text-slate-500">
          <li class="flex items-center gap-2">
            <span class="h-1.5 w-1.5 rounded-full bg-indigo-400" />用例版本 Diff 与一键回滚
          </li>
          <li class="flex items-center gap-2">
            <span class="h-1.5 w-1.5 rounded-full bg-cyan-400" />WebSocket 实时批量执行大盘
          </li>
          <li class="flex items-center gap-2">
            <span class="h-1.5 w-1.5 rounded-full bg-emerald-400" />规则硬校验 + LLM 混合裁决
          </li>
        </ul>
      </div>

      <div class="card w-full p-6 sm:p-7">
        <h2 class="text-lg font-semibold text-slate-100">管理员登录</h2>
        <p class="mt-1 text-xs text-slate-500">使用平台账号进入测试控制台</p>

        <form class="mt-6 space-y-4" @submit.prevent="submit">
          <div>
            <label class="label" for="username">用户名</label>
            <input
              id="username"
              v-model="username"
              class="input"
              autocomplete="username"
              placeholder="admin"
              :disabled="auth.loading"
            />
          </div>
          <div>
            <label class="label" for="password">密码</label>
            <input
              id="password"
              v-model="password"
              class="input"
              type="password"
              autocomplete="current-password"
              placeholder="••••••••"
              :disabled="auth.loading"
            />
          </div>

          <p
            v-if="localError"
            class="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300"
          >
            {{ localError }}
          </p>

          <button type="submit" class="btn-primary w-full" :disabled="auth.loading">
            <svg
              v-if="auth.loading"
              class="h-4 w-4 animate-spin"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path stroke-linecap="round" d="M12 3a9 9 0 1 0 9 9" />
            </svg>
            {{ auth.loading ? '登录中…' : '进入控制台' }}
          </button>
        </form>

        <p class="mt-5 rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 text-[11px] leading-relaxed text-slate-500">
          演示账号：<span class="text-slate-300">admin / admin123</span><br />
          默认连接本机 MySQL 与 Redis，Mock 服务端口 8001 / 8002。
        </p>
      </div>
    </div>
  </div>
</template>

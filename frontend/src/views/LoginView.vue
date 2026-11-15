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
  <div class="relative flex min-h-screen items-center justify-center overflow-hidden bg-canvas px-4 py-10">
    <div class="pointer-events-none absolute -left-32 top-0 h-80 w-80 rounded-full bg-indigo-200/40 blur-3xl" />
    <div class="pointer-events-none absolute -right-28 bottom-0 h-80 w-80 rounded-full bg-sky-200/40 blur-3xl" />

    <div class="relative grid w-full max-w-5xl gap-8 lg:grid-cols-[1.05fr_1fr] lg:items-center">
      <div class="hidden lg:block">
        <div class="flex items-center gap-3">
          <div
            class="flex h-11 w-11 items-center justify-center rounded-[13px] bg-gradient-to-br from-indigo-500 to-indigo-600 text-lg font-bold text-white shadow-[0_12px_28px_-16px_rgba(79,70,229,0.9)]"
          >
            问
          </div>
          <div>
            <p class="text-sm font-semibold leading-tight text-ink">问账 QA</p>
            <p class="text-[9.5px] font-medium uppercase tracking-[0.14em] text-slate-600">Billing QA Console</p>
          </div>
        </div>

        <h1 class="mt-8 text-[34px] font-semibold leading-[1.18] tracking-[-0.03em] text-ink">
          问账智能客服<br />批量测试平台
        </h1>
        <p class="mt-4 max-w-md text-[13px] leading-relaxed text-slate-500">
          面向账单问答场景的自动化回归工作台：版本快照绑定、静态/动态多轮模拟、三重熔断、
          多裁判混合裁决与全链路 Trace 追踪。
        </p>

        <ul class="mt-7 grid gap-2.5 text-xs text-slate-600">
          <li class="flex items-center gap-2.5 rounded-xl border border-line bg-white px-3 py-2.5 shadow-tile">
            <span class="h-1.5 w-1.5 rounded-full bg-indigo-500" aria-hidden="true" />用例版本 Diff 与一键回滚
          </li>
          <li class="flex items-center gap-2.5 rounded-xl border border-line bg-white px-3 py-2.5 shadow-tile">
            <span class="h-1.5 w-1.5 rounded-full bg-sky-500" aria-hidden="true" />WebSocket 实时批量执行大盘
          </li>
          <li class="flex items-center gap-2.5 rounded-xl border border-line bg-white px-3 py-2.5 shadow-tile">
            <span class="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden="true" />规则硬校验 + LLM 混合裁决
          </li>
        </ul>
      </div>

      <div class="card w-full p-6 sm:p-7">
        <p class="eyebrow">Sign in · 控制台</p>
        <h2 class="mt-2 text-xl font-semibold tracking-[-0.02em] text-ink">管理员登录</h2>
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
            class="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
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

        <p class="mt-5 rounded-xl border border-line-soft bg-canvas px-3 py-2.5 text-[11px] leading-relaxed text-slate-600">
          演示账号：<span class="font-medium text-ink-soft">admin / admin123</span><br />
          默认连接本机 PostgreSQL 与 Redis，Mock 服务端口 8001 / 8002。
        </p>
      </div>
    </div>
  </div>
</template>

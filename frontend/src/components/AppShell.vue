<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

interface NavItem {
  name: string
  label: string
  path: string
  icon: string
}

const navItems: NavItem[] = [
  {
    name: 'dashboard',
    label: '测试大盘',
    path: '/',
    icon: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
  },
  {
    name: 'cases',
    label: '用例资产',
    path: '/cases',
    icon: 'M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2m-6 9 2 2 4-4',
  },
  {
    name: 'prompts',
    label: 'Prompt 资产',
    path: '/prompts',
    icon: 'm8 9-3 3 3 3m8-6 3 3-3 3M14 5l-4 14',
  },
  {
    name: 'trace',
    label: '执行记录',
    path: '/trace',
    icon: 'M12 8v4l3 2M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z',
  },
  {
    name: 'settings',
    label: '环境与模型',
    path: '/settings',
    icon: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7.4-3c0-.34-.03-.67-.1-1l2-1.6-2-3.4-2.4 1a7.5 7.5 0 0 0-1.7-1L14.8 3h-4l-.4 2.6a7.5 7.5 0 0 0-1.7 1l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2l-2 1.6 2 3.4 2.4-1a7.5 7.5 0 0 0 1.7 1l.4 2.6h4l.4-2.6a7.5 7.5 0 0 0 1.7-1l2.4 1 2-3.4-2-1.6c.07-.33.1-.66.1-1Z',
  },
]

const activeName = computed(() => (route.name as string) ?? 'dashboard')

function logout(): void {
  auth.logout()
  void router.push({ name: 'login' })
}
</script>

<template>
  <div class="flex min-h-screen">
    <aside
      class="sticky top-0 flex h-screen w-[68px] shrink-0 flex-col border-r border-slate-800/80 bg-slate-950/70 backdrop-blur lg:w-60"
    >
      <div class="flex h-16 items-center gap-2.5 border-b border-slate-800/80 px-3 lg:px-5">
        <div
          class="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-cyan-400 text-sm font-bold text-white shadow-lg shadow-indigo-500/30"
        >
          问
        </div>
        <div class="hidden min-w-0 lg:block">
          <p class="truncate text-sm font-semibold text-slate-100">问账 · 批量测试</p>
          <p class="truncate text-[11px] text-slate-500">Billing QA Console</p>
        </div>
      </div>

      <nav class="flex-1 space-y-1 overflow-y-auto px-2 py-4 lg:px-3">
        <RouterLink
          v-for="item in navItems"
          :key="item.name"
          :to="item.path"
          class="group flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition"
          :class="
            activeName === item.name
              ? 'bg-indigo-500/15 text-indigo-200 ring-1 ring-inset ring-indigo-500/30'
              : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-100'
          "
          :title="item.label"
        >
          <svg class="h-5 w-5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">
            <path stroke-linecap="round" stroke-linejoin="round" :d="item.icon" />
          </svg>
          <span class="hidden lg:inline">{{ item.label }}</span>
        </RouterLink>
      </nav>

      <div class="border-t border-slate-800/80 p-2 lg:p-3">
        <div class="flex items-center gap-2 rounded-xl px-2 py-2">
          <div
            class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-800 text-xs font-semibold text-slate-300"
          >
            {{ (auth.username || 'A').slice(0, 1).toUpperCase() }}
          </div>
          <div class="hidden min-w-0 flex-1 lg:block">
            <p class="truncate text-xs font-medium text-slate-200">{{ auth.username || 'admin' }}</p>
            <p class="text-[10px] text-slate-500">管理员</p>
          </div>
          <button
            class="hidden rounded-lg p-1.5 text-slate-500 transition hover:bg-slate-800 hover:text-rose-300 lg:block"
            title="退出登录"
            @click="logout"
          >
            <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
              <path stroke-linecap="round" stroke-linejoin="round" d="M15 12H4m0 0 3-3m-3 3 3 3M10 4h7a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-7" />
            </svg>
          </button>
        </div>
      </div>
    </aside>

    <div class="flex min-w-0 flex-1 flex-col">
      <header
        class="sticky top-0 z-30 flex h-16 items-center justify-between gap-4 border-b border-slate-800/80 bg-slate-950/70 px-4 backdrop-blur sm:px-6"
      >
        <div class="min-w-0">
          <h1 class="truncate text-base font-semibold text-slate-100">
            {{ route.meta.title ?? '测试平台' }}
          </h1>
          <p class="hidden text-[11px] text-slate-500 sm:block">
            智能客服回归 · 动态多轮 · 全链路 Trace
          </p>
        </div>
        <div class="flex items-center gap-2">
          <span
            class="hidden items-center gap-1.5 rounded-full border border-emerald-500/25 bg-emerald-500/10 px-2.5 py-1 text-[11px] text-emerald-300 sm:inline-flex"
          >
            <span class="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            API /api/v1
          </span>
          <button class="btn-ghost btn-xs lg:hidden" @click="logout">退出</button>
        </div>
      </header>

      <main class="flex-1 px-4 py-5 sm:px-6 sm:py-6">
        <RouterView />
      </main>

      <footer class="px-4 pb-5 text-center text-[11px] text-slate-600 sm:px-6">
        问账智能客服批量测试平台 · FastAPI + Vue3 · 本地部署版
      </footer>
    </div>
  </div>
</template>

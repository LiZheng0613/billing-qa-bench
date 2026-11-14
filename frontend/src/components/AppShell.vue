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
const pageTitle = computed(() =>
  typeof route.meta.title === 'string' ? route.meta.title : '测试平台',
)

function logout(): void {
  auth.logout()
  void router.push({ name: 'login' })
}
</script>

<template>
  <div class="min-h-screen bg-canvas">
    <div class="flex min-h-screen">
      <aside
        class="sticky top-0 z-40 h-screen w-[252px] shrink-0 py-4 pl-4 max-xl:w-[96px] max-xl:py-3 max-xl:pl-3 max-md:w-[80px] max-md:pl-2"
      >
        <div
          class="flex h-full flex-col gap-3 rounded-tile border border-line bg-white px-3 pb-3 pt-4 shadow-rail max-xl:items-center max-xl:px-2"
        >
          <div class="flex items-center gap-2.5 px-1.5 max-xl:px-0">
            <div
              class="flex h-8 w-8 shrink-0 items-center justify-center rounded-[9px] bg-gradient-to-br from-indigo-500 to-indigo-600 text-[13px] font-bold text-white shadow-[0_6px_14px_-8px_rgba(79,70,229,0.9)]"
            >
              问
            </div>
            <div class="min-w-0 max-xl:hidden">
              <p class="truncate text-[13px] font-semibold leading-tight tracking-[-0.02em] text-ink">
                问账 QA
              </p>
              <p class="truncate text-[9px] font-medium uppercase tracking-[0.12em] text-slate-600">
                Billing QA
              </p>
            </div>
          </div>

          <nav class="flex w-full flex-1 flex-col gap-1 overflow-y-auto max-xl:items-center">
            <p class="px-2.5 pb-1 pt-2 text-[9px] font-semibold uppercase tracking-[0.12em] text-slate-600 max-xl:hidden">
              工作台
            </p>
            <RouterLink
              v-for="item in navItems"
              :key="item.name"
              :to="item.path"
              class="flex h-10 w-full items-center gap-2.5 rounded-[11px] px-2.5 text-[12px] font-medium transition duration-150 max-xl:h-11 max-xl:w-11 max-xl:justify-center max-xl:px-0"
              :class="
                activeName === item.name
                  ? 'bg-brand-soft text-brand'
                  : 'text-slate-500 hover:bg-canvas hover:text-ink'
              "
              :title="item.label"
              :aria-current="activeName === item.name ? 'page' : undefined"
            >
              <svg
                class="h-4 w-4 shrink-0"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                aria-hidden="true"
              >
                <path stroke-linecap="round" stroke-linejoin="round" :d="item.icon" />
              </svg>
              <span class="truncate max-xl:hidden">{{ item.label }}</span>
            </RouterLink>
          </nav>

          <div
            class="flex w-full items-center gap-2 border-t border-line-soft px-1 pt-3 max-xl:flex-col max-xl:gap-2 max-xl:px-0"
          >
            <div
              class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[11px] font-semibold text-slate-600"
              :title="auth.username || 'admin'"
            >
              {{ (auth.username || 'A').slice(0, 1).toUpperCase() }}
            </div>
            <div class="min-w-0 flex-1 max-xl:hidden">
              <p class="truncate text-[11.5px] font-medium leading-tight text-ink">
                {{ auth.username || 'admin' }}
              </p>
              <p class="truncate text-[9.5px] text-slate-600">管理员</p>
            </div>
            <button
              class="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-slate-500 transition hover:bg-canvas hover:text-rose-500"
              title="退出登录"
              aria-label="退出登录"
              @click="logout"
            >
              <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <path stroke-linecap="round" stroke-linejoin="round" d="M15 12H4m0 0 3-3m-3 3 3 3M10 4h7a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-7" />
              </svg>
            </button>
          </div>
        </div>
      </aside>

      <div class="flex min-w-0 flex-1 flex-col">
        <header
          class="sticky top-0 z-30 flex h-16 items-center justify-between gap-4 bg-canvas/85 px-6 backdrop-blur max-md:px-4"
        >
          <div class="flex min-w-0 items-center gap-2 text-[11px] text-slate-600">
            <span class="hidden sm:inline">问账 QA</span>
            <span class="hidden text-slate-300 sm:inline" aria-hidden="true">/</span>
            <span class="truncate font-medium text-ink-soft">{{ pageTitle }}</span>
          </div>
          <div class="flex items-center gap-2">
            <span
              class="hidden items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[10.5px] font-medium text-emerald-700 sm:inline-flex"
            >
              <span class="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden="true" />
              API /api/v1
            </span>
            <button class="btn-ghost btn-xs lg:hidden" @click="logout">退出</button>
          </div>
        </header>

        <main class="mx-auto w-full max-w-[1460px] flex-1 px-6 pb-8 max-md:px-4">
          <RouterView />
        </main>

        <footer class="px-6 pb-6 text-center text-[10.5px] text-slate-600 max-md:px-4">
          问账智能客服批量测试平台 · FastAPI + Vue3 · 本地部署版
        </footer>
      </div>
    </div>
  </div>
</template>

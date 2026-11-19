<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { ApiError } from '../api/client'
import { useToastStore } from '../stores/toast'
import AccessTabs from './AccessTabs.vue'
import type { AuditLog } from '../types'

const toast = useToastStore()

const logs = ref<AuditLog[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const loading = ref(false)

const action = ref('')
const resource = ref('all')
const result = ref('all')
const dateFrom = ref('')
const dateTo = ref('')

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

function errorMessage(exc: unknown): string {
  return exc instanceof ApiError ? exc.message : '加载失败，请稍后重试'
}

function actionLabel(value: string): string {
  const labels: Record<string, string> = {
    'auth.login': '登录',
    'auth.login_failed': '登录失败',
    'auth.logout': '退出登录',
    'auth.change_password': '修改密码',
    'user.create': '新建用户',
    'user.update': '更新用户',
    'user.assign_roles': '角色变更',
    'user.reset_password': '重置密码',
    'user.unlock': '解锁账号',
    'role.create': '新建角色',
    'role.update': '更新角色',
    'role.update_permissions': '权限变更',
    'role.delete': '删除角色',
  }
  return labels[value] ?? value
}

function actionTone(value: string): string {
  if (value.includes('failed')) return 'rose'
  if (value.includes('create') || value.includes('login') || value.includes('unlock')) return 'green'
  if (value.includes('role') || value.includes('permission')) return 'indigo'
  return 'amber'
}

function dateLabel(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const pageData = await api.auditLogs.list({
      page: page.value,
      page_size: pageSize,
      action: action.value || undefined,
      resource: resource.value === 'all' ? undefined : resource.value,
      success: result.value === 'all' ? undefined : result.value === 'success',
      date_from: dateFrom.value ? `${dateFrom.value}T00:00:00` : undefined,
      date_to: dateTo.value ? `${dateTo.value}T23:59:59` : undefined,
    })
    logs.value = pageData.items
    total.value = pageData.total
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

function apply(): void {
  page.value = 1
  void load()
}

function reset(): void {
  action.value = ''
  resource.value = 'all'
  result.value = 'all'
  dateFrom.value = ''
  dateTo.value = ''
  page.value = 1
  void load()
}

function go(next: number): void {
  page.value = Math.min(Math.max(1, next), pageCount.value)
  void load()
}

onMounted(() => {
  void load()
})
</script>

<template>
  <section class="flex items-end justify-between gap-5 pb-4 pt-2 max-md:flex-col max-md:items-start">
    <div>
      <div class="mb-1 flex items-center gap-2.5">
        <p class="eyebrow">Access Control</p>
        <span class="inline-flex h-[18px] items-center gap-1 rounded-full border border-brand-edge bg-brand-soft px-2 text-[8.5px] font-semibold text-brand">
          <svg class="h-2.5 w-2.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="5" y="10" width="14" height="10" rx="2" />
            <path stroke-linecap="round" d="M8 10V7a4 4 0 0 1 8 0v3" />
          </svg>
          仅系统管理员可见
        </span>
      </div>
      <h1 class="text-[23px] font-semibold leading-tight tracking-[-0.035em] text-ink">访问审计</h1>
      <p class="mt-1.5 max-w-xl text-[11.5px] leading-relaxed text-slate-500">
        查看登录、授权与关键业务操作。审计日志只追加，默认保留 180 天。
      </p>
    </div>
    <button class="btn" @click="toast.info('导出审计日志：CSV')">导出</button>
  </section>

  <AccessTabs class="mb-4" />

  <div class="card mb-2.5 flex flex-wrap items-center gap-2 p-2.5">
    <input v-model="dateFrom" type="date" class="h-[30px] rounded-lg border border-line bg-white px-2 text-[10.5px] text-ink-soft outline-none" />
    <span class="text-[9px] text-slate-400">至</span>
    <input v-model="dateTo" type="date" class="h-[30px] rounded-lg border border-line bg-white px-2 text-[10.5px] text-ink-soft outline-none" />
    <input v-model="action" class="h-[30px] w-36 rounded-lg border border-line bg-white px-2 text-[10.5px] text-ink outline-none" placeholder="动作，如 user.create" />
    <select v-model="resource" class="h-[30px] rounded-lg border border-line bg-white px-2 text-[10.5px] text-ink-soft outline-none">
      <option value="all">全部资源</option>
      <option value="user">用户</option>
      <option value="role">角色</option>
      <option value="auth">认证</option>
      <option value="case">用例</option>
      <option value="task">任务</option>
    </select>
    <select v-model="result" class="h-[30px] rounded-lg border border-line bg-white px-2 text-[10.5px] text-ink-soft outline-none">
      <option value="all">全部结果</option>
      <option value="success">成功</option>
      <option value="failed">失败</option>
    </select>
    <button class="btn btn-xs btn-primary" @click="apply">应用筛选</button>
    <button class="btn btn-xs btn-ghost" @click="reset">重置</button>
  </div>

  <div class="card overflow-hidden">
    <div class="flex items-center justify-between border-b border-line-soft px-3.5 py-2.5">
      <div>
        <p class="text-[12px] font-semibold text-ink">访问审计</p>
        <p class="mt-0.5 text-[9px] text-slate-400">只追加记录 · 默认保留 180 天</p>
      </div>
      <button class="btn btn-xs" @click="toast.info('导出当前筛选结果：CSV')">导出 CSV</button>
    </div>

    <div class="overflow-x-auto">
      <table class="min-w-[720px] w-full table-fixed border-collapse">
        <thead>
          <tr class="border-b border-line-soft bg-[#fcfcfd] text-[8.5px] uppercase tracking-[0.1em] text-slate-400">
            <th class="w-36 px-3 py-2.5 text-left">时间</th>
            <th class="w-40 px-3 py-2.5 text-left">操作人</th>
            <th class="w-28 px-3 py-2.5 text-left">动作</th>
            <th class="px-3 py-2.5 text-left">对象</th>
            <th class="w-20 px-3 py-2.5 text-left">结果</th>
            <th class="w-28 px-3 py-2.5 text-left max-lg:hidden">IP</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="log in logs" :key="log.id" class="border-b border-line-soft last:border-0 hover:bg-[#fafaff]">
            <td class="truncate px-3 py-2.5 font-mono text-[9px] text-slate-500">{{ dateLabel(log.created_at) }}</td>
            <td class="px-3 py-2.5">
              <div class="flex items-center gap-2">
                <span class="grid h-6 w-6 place-items-center rounded-md bg-indigo-50 text-[9px] font-bold text-indigo-700">{{ log.actor_username.slice(0, 1) }}</span>
                <span>
                  <span class="block text-[10px] font-semibold text-ink-soft">{{ log.actor_username }}</span>
                  <span class="block text-[8.5px] text-slate-400">用户 #{{ log.actor_id ?? '—' }}</span>
                </span>
              </div>
            </td>
            <td class="px-3 py-2.5">
              <span
                class="inline-flex rounded-md px-1.5 py-0.5 text-[8.5px] font-semibold"
                :class="{
                  'bg-indigo-50 text-indigo-700': actionTone(log.action) === 'indigo',
                  'bg-emerald-50 text-emerald-700': actionTone(log.action) === 'green',
                  'bg-amber-50 text-amber-700': actionTone(log.action) === 'amber',
                  'bg-rose-50 text-rose-700': actionTone(log.action) === 'rose',
                }"
              >
                {{ actionLabel(log.action) }}
              </span>
            </td>
            <td class="truncate px-3 py-2.5 text-[9.5px] text-slate-600">
              {{ log.resource }}<span v-if="log.resource_id" class="font-mono text-slate-400"> #{{ log.resource_id }}</span>
              <span v-if="log.path" class="ml-1 text-[8.5px] text-slate-400">{{ log.path }}</span>
            </td>
            <td class="px-3 py-2.5">
              <span class="inline-flex items-center gap-1 text-[9px] font-semibold" :class="log.success ? 'text-emerald-600' : 'text-rose-600'">
                <span class="h-1.5 w-1.5 rounded-full bg-current" />{{ log.success ? '成功' : '失败' }}
              </span>
            </td>
            <td class="truncate px-3 py-2.5 font-mono text-[9px] text-slate-500 max-lg:hidden">{{ log.ip || '—' }}</td>
          </tr>
          <tr v-if="!logs.length">
            <td colspan="6" class="px-3 py-12 text-center text-[11px] text-slate-400">{{ loading ? '加载中…' : '暂无审计记录' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="flex items-center justify-between gap-3 border-t border-line-soft px-3 py-2.5 text-[9px] text-slate-400">
      <span>共 {{ total }} 条 · 仅展示最近 180 天</span>
      <div class="flex items-center gap-1">
        <button class="btn btn-xs" :disabled="page <= 1" @click="go(page - 1)">‹</button>
        <span class="px-1 text-slate-500">{{ page }} / {{ pageCount }}</span>
        <button class="btn btn-xs" :disabled="page >= pageCount" @click="go(page + 1)">›</button>
      </div>
    </div>
  </div>
</template>


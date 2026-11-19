<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { ApiError } from '../api/client'
import { useToastStore } from '../stores/toast'
import AccessTabs from './AccessTabs.vue'
import type { Permission, Role, RoleDetail } from '../types'

const toast = useToastStore()

const roles = ref<Role[]>([])
const permissions = ref<Permission[]>([])
const selectedRole = ref<RoleDetail | null>(null)
const selectedCodes = ref<string[]>([])
const search = ref('')
const loading = ref(false)
const saving = ref(false)

const GROUP_DEFS = [
  { key: 'case', label: '用例资产', modules: ['case'] },
  { key: 'task', label: '批量任务', modules: ['task', 'execution'] },
  {
    key: 'asset',
    label: '资产与配置',
    modules: [
      'dashboard',
      'prompt',
      'environment',
      'model',
      'capability',
      'caliber',
      'candidate',
      'grader',
      'stats',
    ],
  },
  { key: 'system', label: '用户与系统', modules: ['user', 'role', 'audit'] },
]

const roleIcons: Record<string, string> = {
  SYSTEM_ADMIN: 'M12 3l7 4v5c0 4.4-3 8.4-7 9-4-.6-7-4.6-7-9V7l7-4Z',
  CASE_OPERATOR: 'M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2',
  CASE_REVIEWER: 'm9 12 2 2 4-4M12 3l7 4v5c0 4.4-3 8.4-7 9-4-.6-7-4.6-7-9V7l7-4Z',
  TEST_OPERATOR: 'M5 3v4M3 5h4M6 17v4m-2-2h4M13 3l2.5 6.5L22 12l-6.5 2.5L13 21l-2.5-6.5L4 12l6.5-2.5L13 3Z',
  VIEWER: 'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z',
}

function errorMessage(exc: unknown): string {
  return exc instanceof ApiError ? exc.message : '操作失败，请稍后重试'
}

const groups = computed(() =>
  GROUP_DEFS.map((group) => {
    const items = permissions.value.filter(
      (item) =>
        group.modules.includes(item.module) &&
        (!search.value ||
          item.name.includes(search.value) ||
          item.code.toLowerCase().includes(search.value.toLowerCase())),
    )
    const selected = items.filter((item) => selectedCodes.value.includes(item.code)).length
    return { ...group, items, selected, total: items.length }
  }).filter((group) => group.items.length > 0),
)

const totalSelected = computed(() => selectedCodes.value.length)
const progress = computed(() =>
  permissions.value.length
    ? Math.round((selectedCodes.value.length / permissions.value.length) * 100)
    : 0,
)
const isAdminRole = computed(() => selectedRole.value?.code === 'SYSTEM_ADMIN')

function roleIcon(code: string): string {
  return roleIcons[code] ?? roleIcons.VIEWER
}

function isChecked(code: string): boolean {
  return selectedCodes.value.includes(code)
}

async function loadRoles(): Promise<void> {
  try {
    roles.value = await api.roles.list()
    const preferred =
      roles.value.find((role) => role.code === 'CASE_REVIEWER') ?? roles.value[0]
    if (preferred) await selectRole(preferred.id)
  } catch (exc) {
    toast.error(errorMessage(exc))
  }
}

async function selectRole(roleId: number): Promise<void> {
  loading.value = true
  try {
    const detail = await api.roles.get(roleId)
    selectedRole.value = detail
    selectedCodes.value = [...detail.permission_codes]
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

function toggleCode(code: string): void {
  if (isAdminRole.value) return
  selectedCodes.value = isChecked(code)
    ? selectedCodes.value.filter((item) => item !== code)
    : [...selectedCodes.value, code]
}

function toggleGroup(modules: string[]): void {
  if (isAdminRole.value) return
  const items = permissions.value.filter((item) => modules.includes(item.module))
  const allChecked = items.every((item) => selectedCodes.value.includes(item.code))
  const codes = new Set(items.map((item) => item.code))
  selectedCodes.value = allChecked
    ? selectedCodes.value.filter((code) => !codes.has(code))
    : [...new Set([...selectedCodes.value, ...codes])]
}

async function save(): Promise<void> {
  if (!selectedRole.value || isAdminRole.value) return
  saving.value = true
  try {
    const detail = await api.roles.setPermissions(
      selectedRole.value.id,
      selectedCodes.value,
    )
    selectedRole.value = detail
    selectedCodes.value = [...detail.permission_codes]
    toast.success('权限已保存，关联用户会话已失效')
    roles.value = await api.roles.list()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    saving.value = false
  }
}

function copyRole(): void {
  toast.info('复制角色：后续可在此接入创建角色表单')
}

onMounted(async () => {
  try {
    permissions.value = await api.roles.permissions()
  } catch (exc) {
    toast.error(errorMessage(exc))
  }
  await loadRoles()
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
      <h1 class="text-[23px] font-semibold leading-tight tracking-[-0.035em] text-ink">角色与权限</h1>
      <p class="mt-1.5 max-w-xl text-[11.5px] leading-relaxed text-slate-500">
        配置内置角色的权限边界。保存后，关联用户的现有会话立即失效。
      </p>
    </div>
    <div class="flex items-center gap-2">
      <button class="btn" @click="copyRole">复制角色</button>
      <button class="btn-primary" @click="toast.info('新建角色：后续可接入创建表单')">新建角色</button>
    </div>
  </section>

  <AccessTabs class="mb-4" />

  <div class="card mb-2.5 flex gap-1.5 overflow-x-auto p-1.5">
    <button
      v-for="role in roles"
      :key="role.id"
      class="flex min-w-[112px] flex-none items-center gap-1.5 rounded-[11px] border p-1.5 text-left transition"
      :class="selectedRole?.id === role.id ? 'border-indigo-200 bg-[#f7f7ff]' : 'border-transparent hover:border-indigo-100 hover:bg-[#fafaff]'"
      @click="selectRole(role.id)"
    >
      <span class="grid h-7 w-7 place-items-center rounded-lg bg-indigo-50 text-indigo-600">
        <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <path stroke-linecap="round" stroke-linejoin="round" :d="roleIcon(role.code)" />
        </svg>
      </span>
      <span class="min-w-0 flex-1">
        <span class="block truncate text-[10.5px] font-semibold text-ink-soft">{{ role.name }}</span>
        <span class="block text-[8.5px] text-slate-400">{{ role.user_count }} 人 · {{ role.permission_count }} 项</span>
      </span>
      <span v-if="selectedRole?.id === role.id" class="grid h-3.5 w-3.5 place-items-center rounded-full bg-indigo-600 text-white">
        <svg class="h-2 w-2" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path stroke-linecap="round" d="m5 12 4 4L19 6" /></svg>
      </span>
    </button>
  </div>

  <div v-if="selectedRole" class="card mb-2.5 flex items-center gap-3 p-3.5">
    <span class="grid h-9 w-9 place-items-center rounded-xl bg-indigo-50 text-indigo-600">
      <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
        <path stroke-linecap="round" stroke-linejoin="round" :d="roleIcon(selectedRole.code)" />
      </svg>
    </span>
    <div class="min-w-0">
      <div class="flex flex-wrap items-center gap-1.5">
        <p class="text-[14px] font-semibold text-ink">{{ selectedRole.name }}</p>
        <span class="rounded-full border border-indigo-200 bg-indigo-50 px-1.5 py-0.5 text-[8px] font-semibold text-indigo-700">内置</span>
        <span class="rounded-full border border-line bg-slate-50 px-1.5 py-0.5 text-[8px] text-slate-500">{{ selectedRole.user_count }} 个用户</span>
      </div>
      <p class="mt-0.5 font-mono text-[9px] text-slate-400">{{ selectedRole.code }}</p>
      <p class="mt-1 text-[9px] text-slate-400">{{ selectedRole.description || '—' }}</p>
    </div>
    <div class="ml-auto flex flex-none items-center gap-3 max-md:hidden">
      <div class="relative grid h-[46px] w-[46px] place-items-center rounded-full" :style="{ background: `conic-gradient(#4f46e5 ${progress}%, #eef2ff 0)` }">
        <span class="absolute inset-1 rounded-full bg-white" />
        <span class="relative text-[8.5px] font-bold text-indigo-700">{{ progress }}%</span>
      </div>
      <div class="text-right">
        <p class="text-[13px] font-semibold text-ink">{{ totalSelected }} / {{ permissions.length }}</p>
        <p class="text-[8.5px] text-slate-400">已授权 / 全部权限</p>
      </div>
    </div>
    <div class="flex flex-none items-center gap-1.5">
      <button class="btn btn-xs btn-ghost" @click="selectRole(selectedRole.id)">重置</button>
      <button class="btn btn-xs btn-primary" :disabled="saving || isAdminRole" @click="save">保存</button>
    </div>
  </div>

  <div class="mb-2.5 flex flex-wrap items-center gap-2">
    <label class="flex h-[30px] items-center gap-1.5 rounded-lg border border-line bg-white px-2.5 text-slate-400 focus-within:border-indigo-200">
      <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7" /><path stroke-linecap="round" d="m20 20-3.5-3.5" /></svg>
      <input v-model="search" class="w-40 border-0 bg-transparent text-[11px] text-ink outline-none" placeholder="搜索权限名称或权限码" />
    </label>
    <span class="text-[9px] text-slate-400">{{ permissions.length }} 项权限 · {{ groups.length }} 个模块组</span>
    <span v-if="isAdminRole" class="ml-auto rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-[8.5px] text-amber-800">系统管理员权限固定为全量</span>
  </div>

  <div class="columns-1 gap-2.5 lg:columns-2">
    <article v-for="group in groups" :key="group.key" class="card mb-2.5 break-inside-avoid p-3">
      <div class="flex items-center gap-2">
        <span class="grid h-7 w-7 place-items-center rounded-lg bg-indigo-50 text-indigo-600">
          <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <path stroke-linecap="round" stroke-linejoin="round" d="M4 7h16M4 12h10M4 17h7" />
          </svg>
        </span>
        <div class="min-w-0">
          <p class="text-[11px] font-semibold text-ink">{{ group.label }}</p>
          <p class="text-[8.5px] text-slate-400">{{ group.selected }} / {{ group.total }} 项</p>
        </div>
        <button class="ml-auto text-[9px] font-semibold text-slate-400 hover:text-indigo-600" :disabled="isAdminRole" @click="toggleGroup(group.modules)">全选</button>
      </div>
      <div class="mt-2 h-[3px] overflow-hidden rounded-full bg-slate-100">
        <div class="h-full rounded-full bg-indigo-400" :style="{ width: group.total ? `${(group.selected / group.total) * 100}%` : '0%' }" />
      </div>
      <div class="mt-1">
        <label
          v-for="item in group.items"
          :key="item.code"
          class="flex cursor-pointer items-center gap-2 border-t border-line-soft px-1 py-1.5 first:border-t-0 hover:bg-[#fcfcff]"
        >
          <span class="min-w-0 flex-1">
            <span class="block text-[10px] text-ink-soft">{{ item.name }}</span>
            <span class="block truncate font-mono text-[8px] text-slate-400">{{ item.code }}</span>
          </span>
          <label class="relative inline-flex h-4 w-7 flex-none cursor-pointer items-center">
            <input
              type="checkbox"
              class="peer sr-only"
              :checked="isChecked(item.code)"
              :disabled="isAdminRole"
              @change="toggleCode(item.code)"
            />
            <span class="h-4 w-7 rounded-full bg-slate-200 transition peer-checked:bg-indigo-600 peer-disabled:opacity-50" />
            <span class="absolute left-0.5 h-3 w-3 rounded-full bg-white shadow transition peer-checked:translate-x-3" />
          </label>
        </label>
      </div>
    </article>
  </div>

  <div class="mt-2.5 flex items-center justify-between rounded-xl border border-line bg-white px-3 py-2.5 shadow-panel">
    <div class="flex items-center gap-1.5 text-[9px] text-slate-400">
      <span class="h-1.5 w-1.5 rounded-full bg-emerald-500" />
      已保存 · 权限变更后关联用户会话立即失效
    </div>
    <div class="flex gap-1.5">
      <button class="btn btn-xs btn-ghost" @click="selectedRole && selectRole(selectedRole.id)">取消</button>
      <button class="btn btn-xs btn-primary" :disabled="saving || !selectedRole || isAdminRole" @click="save">保存权限</button>
    </div>
  </div>
</template>


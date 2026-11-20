<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../api'
import { ApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'
import { useToastStore } from '../stores/toast'
import AccessTabs from './AccessTabs.vue'
import type {
  Permission,
  Role,
  SystemUser,
  SystemUserDetail,
} from '../types'

const auth = useAuthStore()
const toast = useToastStore()

const users = ref<SystemUser[]>([])
const roles = ref<Role[]>([])
const permissions = ref<Permission[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const keyword = ref('')
const statusFilter = ref('all')
const roleFilter = ref<'all' | number>('all')
const loading = ref(false)

const selectedIds = ref<number[]>([])
const drawerOpen = ref(false)
const createMode = ref(false)
const selected = ref<SystemUserDetail | null>(null)
const selectedRoleIds = ref<number[]>([])
const effectiveCodes = ref<string[]>([])
const tempPassword = ref<string | null>(null)
const saving = ref(false)

const createForm = reactive({
  username: '',
  display_name: '',
  email: '',
  phone: '',
  password: '',
  role_ids: [] as number[],
})

const permissionMap = computed(
  () => new Map(permissions.value.map((item) => [item.code, item])),
)

const effectiveChips = computed(() =>
  effectiveCodes.value
    .slice(0, 5)
    .map((code) => permissionMap.value.get(code)?.name ?? code),
)

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const allChecked = computed(
  () => users.value.length > 0 && users.value.every((user) => selectedIds.value.includes(user.id)),
)

function errorMessage(exc: unknown): string {
  return exc instanceof ApiError ? exc.message : '操作失败，请稍后重试'
}

function roleTone(code: string): string {
  const map: Record<string, string> = {
    SYSTEM_ADMIN: 'indigo',
    CASE_OPERATOR: 'violet',
    CASE_REVIEWER: 'amber',
    TEST_EXECUTOR: 'emerald',
    TEST_OPERATOR: 'emerald',
    VIEWER: 'slate',
  }
  return map[code] ?? 'slate'
}

function statusTone(status: string): string {
  if (status === 'ACTIVE') return 'text-emerald-700 bg-emerald-50 border-emerald-200'
  if (status === 'DISABLED') return 'text-slate-500 bg-slate-100 border-slate-200'
  return 'text-amber-700 bg-amber-50 border-amber-200'
}

function statusLabel(status: string): string {
  if (status === 'ACTIVE') return '启用'
  if (status === 'DISABLED') return '已禁用'
  return '已锁定'
}

function dateLabel(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function dayLabel(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString('zh-CN')
}

async function loadRoles(): Promise<void> {
  try {
    roles.value = await api.roles.list()
  } catch (exc) {
    toast.error(errorMessage(exc))
  }
}

async function loadPermissions(): Promise<void> {
  try {
    permissions.value = await api.roles.permissions()
  } catch (exc) {
    toast.error(errorMessage(exc))
  }
}

async function loadUsers(): Promise<void> {
  loading.value = true
  try {
    const result = await api.users.list({
      keyword: keyword.value || undefined,
      status: statusFilter.value === 'all' ? undefined : statusFilter.value,
      role_id: roleFilter.value === 'all' ? undefined : roleFilter.value,
      page: page.value,
      page_size: pageSize,
    })
    users.value = result.items
    total.value = result.total
    selectedIds.value = selectedIds.value.filter((id) =>
      result.items.some((user) => user.id === id),
    )
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

async function loadEffectivePermissions(): Promise<void> {
  if (!selectedRoleIds.value.length) {
    effectiveCodes.value = []
    return
  }
  try {
    const details = await Promise.all(
      selectedRoleIds.value.map((id) => api.roles.get(id)),
    )
    effectiveCodes.value = [...new Set(details.flatMap((item) => item.permission_codes))]
  } catch {
    effectiveCodes.value = []
  }
}

async function openUser(user: SystemUser): Promise<void> {
  createMode.value = false
  drawerOpen.value = true
  selected.value = null
  selectedRoleIds.value = user.roles.map((role) => role.id)
  tempPassword.value = null
  try {
    const detail = await api.users.get(user.id)
    selected.value = detail
    selectedRoleIds.value = detail.roles.map((role) => role.id)
    if (!selectedIds.value.includes(user.id)) selectedIds.value.push(user.id)
    await loadEffectivePermissions()
  } catch (exc) {
    toast.error(errorMessage(exc))
  }
}

function openCreate(): void {
  createMode.value = true
  drawerOpen.value = true
  selected.value = null
  selectedRoleIds.value = []
  effectiveCodes.value = []
  tempPassword.value = null
  createForm.username = ''
  createForm.display_name = ''
  createForm.email = ''
  createForm.phone = ''
  createForm.password = ''
  const defaultRole = roles.value.find((role) => role.code === 'CASE_OPERATOR')
  createForm.role_ids = defaultRole ? [defaultRole.id] : []
}

function closeDrawer(): void {
  drawerOpen.value = false
  createMode.value = false
  tempPassword.value = null
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && drawerOpen.value) closeDrawer()
}

function toggleSelect(userId: number): void {
  selectedIds.value = selectedIds.value.includes(userId)
    ? selectedIds.value.filter((id) => id !== userId)
    : [...selectedIds.value, userId]
}

function toggleAll(): void {
  selectedIds.value = allChecked.value ? [] : users.value.map((user) => user.id)
}

async function toggleRole(roleId: number): Promise<void> {
  selectedRoleIds.value = selectedRoleIds.value.includes(roleId)
    ? selectedRoleIds.value.filter((id) => id !== roleId)
    : [...selectedRoleIds.value, roleId]
  await loadEffectivePermissions()
}

async function saveRoles(): Promise<void> {
  if (!selected.value) return
  saving.value = true
  try {
    const updated = await api.users.setRoles(selected.value.id, selectedRoleIds.value)
    selected.value = { ...selected.value, ...updated }
    toast.success('角色已保存，关联用户会话已失效')
    await loadUsers()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    saving.value = false
  }
}

async function saveCreate(): Promise<void> {
  if (!createForm.username.trim() || !createForm.display_name.trim() || !createForm.password) {
    toast.error('请填写用户名、展示名和初始密码')
    return
  }
  saving.value = true
  try {
    await api.users.create({
      username: createForm.username.trim(),
      display_name: createForm.display_name.trim(),
      email: createForm.email.trim() || null,
      phone: createForm.phone.trim() || null,
      password: createForm.password,
      role_ids: createForm.role_ids,
    })
    toast.success('用户已创建')
    closeDrawer()
    await loadUsers()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    saving.value = false
  }
}

async function setStatus(status: 'ACTIVE' | 'DISABLED'): Promise<void> {
  if (!selected.value) return
  saving.value = true
  try {
    selected.value = await api.users.update(selected.value.id, { status })
    toast.success(status === 'ACTIVE' ? '账号已启用' : '账号已禁用，会话已失效')
    await loadUsers()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    saving.value = false
  }
}

async function unlock(): Promise<void> {
  if (!selected.value) return
  saving.value = true
  try {
    selected.value = await api.users.unlock(selected.value.id)
    toast.success('账号已解锁')
    await loadUsers()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    saving.value = false
  }
}

async function resetPassword(): Promise<void> {
  if (!selected.value) return
  saving.value = true
  try {
    const result = await api.users.resetPassword(selected.value.id)
    tempPassword.value = result.temp_password
    toast.success('临时密码已生成')
    await loadUsers()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    saving.value = false
  }
}

async function bulkDisable(): Promise<void> {
  if (!selectedIds.value.length) return
  const ids = selectedIds.value.filter((id) => id !== auth.user?.id)
  if (!ids.length) {
    toast.error('不能禁用当前登录账号')
    return
  }
  saving.value = true
  try {
    await Promise.all(ids.map((id) => api.users.update(id, { status: 'DISABLED' })))
    toast.success(`已禁用 ${ids.length} 个账号`)
    selectedIds.value = []
    await loadUsers()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    saving.value = false
  }
}

async function copyTempPassword(): Promise<void> {
  if (!tempPassword.value) return
  try {
    await navigator.clipboard.writeText(tempPassword.value)
    toast.success('临时密码已复制')
  } catch {
    toast.error('复制失败，请手动选择密码')
  }
}

let searchTimer: number | undefined

watch(keyword, () => {
  window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => {
    page.value = 1
    void loadUsers()
  }, 300)
})

watch([statusFilter, roleFilter], () => {
  page.value = 1
  void loadUsers()
})

watch(page, () => void loadUsers())

onMounted(async () => {
  document.addEventListener('keydown', onKeydown)
  await Promise.all([loadRoles(), loadPermissions(), loadUsers()])
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
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
      <h1 class="text-[23px] font-semibold leading-tight tracking-[-0.035em] text-ink">用户管理</h1>
      <p class="mt-1.5 max-w-xl text-[11.5px] leading-relaxed text-slate-500">
        管理当前系统的全部账号。点击用户行打开右侧详情，支持批量勾选与批量禁用。
      </p>
    </div>
    <div class="flex items-center gap-2 max-md:w-full">
      <button class="btn" @click="toast.info('导出功能待接入')">
        <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 3v12m0 0 4-4m-4 4-4-4M5 21h14" />
        </svg>
        导出
      </button>
      <button class="btn-primary max-md:ml-auto" @click="openCreate">
        <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" d="M12 5v14M5 12h14" />
        </svg>
        新建用户
      </button>
    </div>
  </section>

  <AccessTabs class="mb-4" />

  <div class="card overflow-hidden">
    <div class="flex flex-wrap items-center gap-2 border-b border-line-soft px-3 py-2.5">
      <label class="flex h-[30px] items-center gap-1.5 rounded-lg border border-line bg-white px-2.5 text-slate-400 focus-within:border-indigo-200">
        <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="7" />
          <path stroke-linecap="round" d="m20 20-3.5-3.5" />
        </svg>
        <input
          v-model="keyword"
          class="w-40 border-0 bg-transparent text-[11px] text-ink outline-none"
          placeholder="搜索姓名或用户名（分号分隔多词）"
        />
      </label>
      <select v-model="roleFilter" class="h-[30px] rounded-lg border border-line bg-white px-2 text-[11px] text-ink-soft outline-none">
        <option value="all">全部角色</option>
        <option v-for="role in roles" :key="role.id" :value="role.id">{{ role.name }}</option>
      </select>
      <select v-model="statusFilter" class="h-[30px] rounded-lg border border-line bg-white px-2 text-[11px] text-ink-soft outline-none">
        <option value="all">全部状态</option>
        <option value="ACTIVE">启用</option>
        <option value="DISABLED">已禁用</option>
        <option value="LOCKED">已锁定</option>
      </select>
      <span class="text-[9px] text-slate-400">点击行查看详情</span>
      <span class="ml-auto text-[9.5px] text-slate-500">已选 {{ selectedIds.length }} 项</span>
      <button class="btn btn-xs" :disabled="!selectedIds.length || saving" @click="bulkDisable">批量禁用</button>
    </div>

    <div class="overflow-x-auto">
      <table class="min-w-[760px] w-full border-collapse text-left">
        <thead>
          <tr class="border-b border-line-soft bg-[#fcfcfd] text-[8.5px] uppercase tracking-[0.1em] text-slate-400">
            <th class="w-8 px-3 py-2.5">
              <input
                type="checkbox"
                class="h-3.5 w-3.5 rounded border-slate-300 accent-indigo-600"
                :checked="allChecked"
                @change="toggleAll"
              />
            </th>
            <th class="px-3 py-2.5">用户</th>
            <th class="px-3 py-2.5">角色</th>
            <th class="px-3 py-2.5">状态</th>
            <th class="px-3 py-2.5 max-lg:hidden">最近登录</th>
            <th class="px-3 py-2.5 max-xl:hidden">创建时间</th>
            <th class="w-14 px-3 py-2.5">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="user in users"
            :key="user.id"
            class="cursor-pointer border-b border-line-soft transition last:border-0 hover:bg-[#fafaff]"
            :class="selected?.id === user.id ? 'bg-[#f7f7ff] shadow-[inset_2px_0_0_#4f46e5]' : ''"
            @click="openUser(user)"
          >
            <td class="px-3 py-2.5" @click.stop>
              <input
                type="checkbox"
                class="h-3.5 w-3.5 rounded border-slate-300 accent-indigo-600"
                :checked="selectedIds.includes(user.id)"
                @change="toggleSelect(user.id)"
              />
            </td>
            <td class="px-3 py-2.5">
              <div class="flex items-center gap-2">
                <span class="grid h-7 w-7 place-items-center rounded-lg bg-indigo-50 text-[10px] font-bold text-indigo-700">
                  {{ user.display_name.slice(0, 1) }}
                </span>
                <span>
                  <span class="block text-[11px] font-semibold text-ink">{{ user.display_name }}</span>
                  <span class="block text-[9px] text-slate-400">{{ user.username }}</span>
                </span>
              </div>
            </td>
            <td class="px-3 py-2.5">
              <span
                v-for="role in user.roles.slice(0, 1)"
                :key="role.id"
                class="inline-flex items-center rounded-full border px-2 py-0.5 text-[9px] font-semibold"
                :class="{
                  'border-indigo-200 bg-indigo-50 text-indigo-700': roleTone(role.code) === 'indigo',
                  'border-violet-200 bg-violet-50 text-violet-700': roleTone(role.code) === 'violet',
                  'border-amber-200 bg-amber-50 text-amber-700': roleTone(role.code) === 'amber',
                  'border-emerald-200 bg-emerald-50 text-emerald-700': roleTone(role.code) === 'emerald',
                  'border-slate-200 bg-slate-100 text-slate-600': roleTone(role.code) === 'slate',
                }"
              >
                {{ role.name }}
              </span>
              <span v-if="user.roles.length > 1" class="ml-1 text-[9px] text-slate-400">+{{ user.roles.length - 1 }}</span>
            </td>
            <td class="px-3 py-2.5">
              <span class="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[9px] font-semibold" :class="statusTone(user.status)">
                <span class="h-1.5 w-1.5 rounded-full bg-current" />
                {{ statusLabel(user.status) }}
              </span>
            </td>
            <td class="px-3 py-2.5 text-[9.5px] text-slate-500 max-lg:hidden">{{ dateLabel(user.last_login_at) }}</td>
            <td class="px-3 py-2.5 text-[9.5px] text-slate-500 max-xl:hidden">{{ dayLabel(user.created_at) }}</td>
            <td class="px-3 py-2.5" @click.stop>
              <button class="btn btn-xs btn-ghost" @click="openUser(user)">详情</button>
            </td>
          </tr>
          <tr v-if="!users.length">
            <td colspan="7" class="px-3 py-12 text-center text-[11px] text-slate-400">
              {{ loading ? '加载中…' : '暂无用户' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="flex items-center justify-between gap-3 border-t border-line-soft px-3 py-2.5 text-[9px] text-slate-400">
      <span>共 {{ total }} 个账号</span>
      <div class="flex items-center gap-1">
        <button class="btn btn-xs" :disabled="page <= 1" @click="page -= 1">‹</button>
        <span class="px-1 text-slate-500">{{ page }} / {{ pageCount }}</span>
        <button class="btn btn-xs" :disabled="page >= pageCount" @click="page += 1">›</button>
      </div>
    </div>
  </div>

  <Teleport to="body">
    <Transition name="drawer">
      <div v-if="drawerOpen" class="fixed inset-0 z-50 pointer-events-none">
        <div class="absolute inset-0 bg-slate-900/10 pointer-events-auto" @click="closeDrawer" />
        <aside class="pointer-events-auto absolute right-4 top-20 bottom-4 flex w-[380px] max-w-[calc(100vw-2rem)] flex-col overflow-hidden rounded-2xl border border-line bg-white shadow-2xl max-md:right-2 max-md:top-16">
          <div class="flex items-center justify-between border-b border-line-soft px-4 py-3">
            <div>
              <p class="text-[11px] font-semibold text-ink">{{ createMode ? '新建用户' : '用户详情' }}</p>
              <p class="mt-0.5 text-[9px] text-slate-400">{{ createMode ? '账号创建后默认要求首次登录改密' : '账号、角色与权限' }}</p>
            </div>
            <button class="grid h-7 w-7 place-items-center rounded-lg text-slate-400 hover:bg-canvas hover:text-ink" @click="closeDrawer">
              <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" d="M6 6l12 12M18 6 6 18" />
              </svg>
            </button>
          </div>

          <div v-if="tempPassword" class="mx-4 mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5">
            <p class="text-[9px] font-semibold text-amber-800">临时密码（仅展示一次）</p>
            <div class="mt-1 flex items-center gap-2">
              <code class="flex-1 break-all text-[11px] font-semibold text-amber-900">{{ tempPassword }}</code>
              <button class="btn btn-xs" @click="copyTempPassword">复制</button>
            </div>
          </div>

          <div v-if="createMode" class="flex-1 overflow-y-auto px-4 py-3">
            <div class="space-y-3">
              <label class="block">
                <span class="label">用户名</span>
                <input v-model="createForm.username" class="input" placeholder="如 zhangsan" />
              </label>
              <label class="block">
                <span class="label">展示名</span>
                <input v-model="createForm.display_name" class="input" placeholder="如 张三" />
              </label>
              <label class="block">
                <span class="label">初始密码</span>
                <input v-model="createForm.password" type="password" class="input" placeholder="请输入初始密码" />
              </label>
              <div class="grid grid-cols-2 gap-2">
                <label class="block">
                  <span class="label">邮箱</span>
                  <input v-model="createForm.email" class="input" placeholder="可选" />
                </label>
                <label class="block">
                  <span class="label">手机</span>
                  <input v-model="createForm.phone" class="input" placeholder="可选" />
                </label>
              </div>
              <div>
                <span class="label">角色</span>
                <div class="mt-1 space-y-1">
                  <label v-for="role in roles" :key="role.id" class="flex cursor-pointer items-start gap-2 rounded-lg border border-transparent px-2 py-1.5 hover:border-indigo-100 hover:bg-indigo-50/40">
                    <input v-model="createForm.role_ids" type="checkbox" :value="role.id" class="mt-0.5 h-3.5 w-3.5 rounded border-slate-300 accent-indigo-600" />
                    <span><span class="block text-[10px] font-semibold text-ink-soft">{{ role.name }}</span><span class="block text-[9px] text-slate-400">{{ role.description || role.code }}</span></span>
                  </label>
                </div>
              </div>
            </div>
          </div>

          <div v-else-if="selected" class="flex-1 overflow-y-auto">
            <div class="flex items-center gap-3 border-b border-line-soft px-4 py-3">
              <span class="grid h-11 w-11 place-items-center rounded-xl bg-amber-50 text-[14px] font-bold text-amber-700">{{ selected.display_name.slice(0, 1) }}</span>
              <div class="min-w-0">
                <div class="flex items-center gap-1.5">
                  <p class="text-[13px] font-semibold text-ink">{{ selected.display_name }}</p>
                  <span v-if="selected.roles[0]" class="rounded-full border border-indigo-200 bg-indigo-50 px-2 py-0.5 text-[8.5px] font-semibold text-indigo-700">{{ selected.roles[0].name }}</span>
                </div>
                <p class="mt-0.5 text-[9.5px] text-slate-400">{{ selected.username }}</p>
                <span class="mt-1 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[8.5px] font-semibold" :class="statusTone(selected.status)">
                  <span class="h-1.5 w-1.5 rounded-full bg-current" />{{ statusLabel(selected.status) }}
                </span>
              </div>
            </div>

            <div class="flex gap-1.5 px-4 pt-3">
              <button class="btn btn-xs" :disabled="saving" @click="resetPassword">重置密码</button>
              <button v-if="selected.status === 'LOCKED'" class="btn btn-xs" :disabled="saving" @click="unlock">解锁</button>
              <button
                v-else
                class="btn btn-xs"
                :class="selected.status === 'ACTIVE' ? 'text-rose-600 border-rose-200' : ''"
                :disabled="saving"
                @click="setStatus(selected.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE')"
              >
                {{ selected.status === 'ACTIVE' ? '禁用账号' : '启用账号' }}
              </button>
            </div>

            <div class="px-4 py-3">
              <p class="mb-1.5 text-[8.5px] font-semibold uppercase tracking-[0.1em] text-slate-400">基础信息</p>
              <div class="grid grid-cols-2 gap-2.5">
                <div><p class="text-[8.5px] text-slate-400">邮箱</p><p class="mt-0.5 truncate text-[10px] text-ink-soft">{{ selected.email || '—' }}</p></div>
                <div><p class="text-[8.5px] text-slate-400">手机</p><p class="mt-0.5 text-[10px] text-ink-soft">{{ selected.phone || '—' }}</p></div>
                <div><p class="text-[8.5px] text-slate-400">最近登录</p><p class="mt-0.5 text-[10px] text-ink-soft">{{ dateLabel(selected.last_login_at) }}</p></div>
                <div><p class="text-[8.5px] text-slate-400">创建时间</p><p class="mt-0.5 text-[10px] text-ink-soft">{{ dayLabel(selected.created_at) }}</p></div>
                <div><p class="text-[8.5px] text-slate-400">登录 IP</p><p class="mt-0.5 text-[9.5px] font-mono text-ink-soft">{{ selected.last_login_ip || '—' }}</p></div>
                <div><p class="text-[8.5px] text-slate-400">用户 ID</p><p class="mt-0.5 text-[9.5px] font-mono text-ink-soft">usr_{{ String(selected.id).padStart(3, '0') }}</p></div>
              </div>
            </div>

            <div class="border-t border-line-soft px-4 py-3">
              <div class="mb-1.5 flex items-center justify-between">
                <p class="text-[8.5px] font-semibold uppercase tracking-[0.1em] text-slate-400">角色</p>
                <span class="text-[8.5px] text-slate-400">已选 {{ selectedRoleIds.length }} 个</span>
              </div>
              <div class="space-y-0.5">
                <label v-for="role in roles" :key="role.id" class="flex cursor-pointer items-start gap-2 rounded-lg border border-transparent px-2 py-1.5 hover:border-indigo-100 hover:bg-indigo-50/40">
                  <input
                    type="checkbox"
                    class="mt-0.5 h-3.5 w-3.5 rounded border-slate-300 accent-indigo-600"
                    :checked="selectedRoleIds.includes(role.id)"
                    @change="toggleRole(role.id)"
                  />
                  <span class="flex-1"><span class="block text-[10px] font-semibold text-ink-soft">{{ role.name }}</span><span class="block text-[8.5px] text-slate-400">{{ role.description || role.code }}</span></span>
                  <span class="text-[8.5px] text-slate-400">{{ role.user_count }} 人</span>
                </label>
              </div>
            </div>

            <div class="border-t border-line-soft px-4 py-3">
              <div class="mb-1.5 flex items-center justify-between">
                <p class="text-[8.5px] font-semibold uppercase tracking-[0.1em] text-slate-400">生效权限</p>
                <span class="text-[9px] font-semibold text-indigo-600">{{ effectiveCodes.length }} 项</span>
              </div>
              <div class="flex flex-wrap gap-1">
                <span v-for="chip in effectiveChips" :key="chip" class="rounded-md border border-line bg-slate-50 px-2 py-1 text-[8.5px] text-slate-600">{{ chip }}</span>
                <span v-if="effectiveCodes.length > 5" class="rounded-md border border-indigo-200 bg-indigo-50 px-2 py-1 text-[8.5px] font-semibold text-indigo-700">+{{ effectiveCodes.length - 5 }} 项</span>
                <span v-if="!effectiveCodes.length" class="text-[9px] text-slate-400">未分配角色</span>
              </div>
            </div>

            <div class="mx-4 mb-3 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2 text-[9px] leading-relaxed text-amber-800">
              调整角色或禁用账号后，该用户现有登录会话立即失效。
            </div>
          </div>

          <div class="flex justify-end gap-1.5 border-t border-line-soft bg-[#fcfcfd] px-4 py-2.5">
            <button class="btn btn-xs btn-ghost" @click="closeDrawer">取消</button>
            <button v-if="createMode" class="btn btn-xs btn-primary" :disabled="saving" @click="saveCreate">创建用户</button>
            <button v-else class="btn btn-xs btn-primary" :disabled="saving || !selected" @click="saveRoles">保存变更</button>
          </div>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.drawer-enter-active,
.drawer-leave-active {
  transition: opacity 0.2s;
}
.drawer-enter-active aside,
.drawer-leave-active aside {
  transition: transform 0.2s;
}
.drawer-enter-from,
.drawer-leave-to {
  opacity: 0;
}
.drawer-enter-from aside,
.drawer-leave-to aside {
  transform: translateX(16px);
}
</style>


<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import EmptyState from '../components/EmptyState.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import CaseTraceInspector from './components/CaseTraceInspector.vue'
import type { BatchTask, ExecutionDetail } from '../types'
import { errorMessage } from '../utils/errors'
import { useBreadcrumbStore } from '../stores/breadcrumb'
import type { BreadcrumbItem } from '../stores/breadcrumb'
import { useToastStore } from '../stores/toast'

const route = useRoute()
const router = useRouter()
const toast = useToastStore()
const breadcrumb = useBreadcrumbStore()

const detail = ref<ExecutionDetail | null>(null)
const loadingDetail = ref(false)
const relationTask = ref<BatchTask | null>(null)
const loadError = ref('')

const executionId = computed(() => {
  const value = Number(route.query.execution)
  return Number.isFinite(value) && value > 0 ? value : null
})

// 从用例详情进入时优先展示用例路径，否则按批量任务路径展示。
const origin = computed<'task' | 'case'>(() =>
  route.query.from === 'case' ? 'case' : 'task',
)

function syncBreadcrumb(data: ExecutionDetail | null): void {
  if (!data) {
    breadcrumb.clear()
    return
  }
  const trail: BreadcrumbItem[] = []
  if (origin.value === 'case' && data.case_id) {
    trail.push({ label: '用例资产', to: '/cases' })
    trail.push({ label: '用例详情', to: `/cases/${data.case_id}` })
  } else if (data.task_id) {
    trail.push({ label: '批量任务', to: '/tasks' })
    trail.push({ label: '任务详情', to: `/tasks/${data.task_id}` })
  } else if (data.case_id) {
    trail.push({ label: '用例资产', to: '/cases' })
    trail.push({ label: '用例详情', to: `/cases/${data.case_id}` })
  }
  trail.push({ label: '单笔执行结果' })
  breadcrumb.set(trail)
}

async function loadDetail(id: number): Promise<boolean> {
  loadingDetail.value = true
  loadError.value = ''
  try {
    const data = await api.executions.get(id)
    detail.value = data
    relationTask.value = data.task_id
      ? await api.tasks.get(data.task_id).catch(() => null)
      : null
    syncBreadcrumb(data)
    return true
  } catch (exc) {
    detail.value = null
    relationTask.value = null
    loadError.value = errorMessage(exc)
    syncBreadcrumb(null)
    toast.error(loadError.value)
    return false
  } finally {
    loadingDetail.value = false
  }
}

function openTask(): void {
  if (detail.value?.task_id) {
    void router.push({ name: 'task-detail', params: { id: String(detail.value.task_id) } })
  }
}

function openCase(): void {
  if (detail.value?.case_id) {
    void router.push({ name: 'case-detail', params: { id: String(detail.value.case_id) } })
  }
}

function back(): void {
  if (window.history.length > 1) router.back()
  else void router.push({ name: 'dashboard' })
}

// 顶部左上角只保留一个返回入口：按进入来源回到用例详情或所属批量任务详情。
const backLabel = computed(() => {
  if (origin.value === 'case') return '返回用例详情'
  if (detail.value?.task_id) return '返回批量任务详情'
  if (detail.value?.case_id) return '返回用例详情'
  return '返回'
})

function goBack(): void {
  if (origin.value === 'case' && detail.value?.case_id) openCase()
  else if (detail.value?.task_id) openTask()
  else if (detail.value?.case_id) openCase()
  else back()
}

async function refreshDetail(): Promise<void> {
  if (!executionId.value) return
  if (await loadDetail(executionId.value)) toast.success('刷新成功')
}

watch(
  executionId,
  (id) => {
    if (id === null) {
      detail.value = null
      relationTask.value = null
      loadError.value = ''
      syncBreadcrumb(null)
      return
    }
    if (detail.value?.id === id) return
    void loadDetail(id)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  breadcrumb.clear()
})
</script>

<template>
  <div class="space-y-4">
    <template v-if="executionId">
      <button
        class="inline-flex items-center gap-1 text-xs text-slate-500 transition hover:text-ink"
        @click="goBack"
      >
        <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="m15 18-6-6 6-6" />
        </svg>
        {{ backLabel }}
      </button>

      <PageHeader eyebrow="Single run · 单笔执行结果">
        <template #title>
          <h1 class="mt-2 flex flex-wrap items-center gap-2 text-[22px] font-semibold leading-tight tracking-[-0.02em] text-ink">
            执行 #{{ detail?.id ?? executionId }}
            <span v-if="detail?.case_code" class="font-mono text-sm font-medium text-slate-600">
              · {{ detail.case_code }}
            </span>
            <StatusPill v-if="detail" :status="detail.status" size="sm" />
          </h1>
        </template>
        <template #sub>
          <p class="mt-2 text-xs text-slate-600">
            <span v-if="detail?.case_name">{{ detail.case_name }} · </span>
            <span v-if="relationTask">
              所属批量任务
              <button class="font-mono text-indigo-600 underline decoration-dotted" @click="openTask">
                {{ relationTask.code }}
              </button>
              {{ relationTask.name ? `· ${relationTask.name}` : '' }}
            </span>
            <span v-else-if="detail">单笔调试 / 重跑记录</span>
          </p>
        </template>
        <template #actions>
          <button class="btn-ghost" @click="refreshDetail">刷新</button>
        </template>
      </PageHeader>

      <div v-if="loadError && !detail" class="card">
        <EmptyState title="执行记录不存在或已删除" :description="loadError">
          <button class="btn-primary btn-xs mt-2" @click="router.push({ name: 'dashboard' })">
            返回测试大盘
          </button>
        </EmptyState>
      </div>

      <CaseTraceInspector
        v-else
        :detail="detail"
        :loading="loadingDetail"
      />
    </template>

    <div v-else class="card">
      <EmptyState
        title="请选择一条执行记录"
        description="执行记录入口已合并到批量任务详情与用例详情；在任务详情的用例结果或用例详情的历史记录中点击即可查看单笔执行。"
      >
        <div class="mt-3 flex flex-wrap items-center justify-center gap-2">
          <button class="btn-primary btn-xs" @click="router.push({ name: 'tasks' })">前往批量任务</button>
          <button class="btn-ghost btn-xs" @click="router.push({ name: 'cases' })">前往用例资产</button>
        </div>
      </EmptyState>
    </div>
  </div>
</template>

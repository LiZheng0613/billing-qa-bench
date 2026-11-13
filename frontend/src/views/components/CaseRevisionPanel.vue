<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '../../api'
import BaseModal from '../../components/BaseModal.vue'
import EmptyState from '../../components/EmptyState.vue'
import StatusPill from '../../components/StatusPill.vue'
import type { CaseRevision, RevisionDiff, TestCase } from '../../types'
import { errorMessage } from '../../utils/errors'
import { formatDateTime, safeJson, statusLabel } from '../../utils/format'
import { useToastStore } from '../../stores/toast'

const props = defineProps<{ modelValue: boolean; caseData: TestCase | null }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'updated'): void
}>()

const toast = useToastStore()
const revisions = ref<CaseRevision[]>([])
const fromVersion = ref<number | null>(null)
const toVersion = ref<number | null>(null)
const diff = ref<RevisionDiff | null>(null)
const loading = ref(false)
const diffLoading = ref(false)
const rolling = ref(false)

const versions = computed(() => revisions.value.map((item) => item.version))

function display(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value || '（空字符串）'
  if (typeof value === 'object') return safeJson(value, 0)
  return String(value)
}

async function load(): Promise<void> {
  if (!props.caseData) return
  loading.value = true
  diff.value = null
  try {
    revisions.value = await api.cases.revisions(props.caseData.id, false)
    if (revisions.value.length >= 2) {
      toVersion.value = revisions.value[0].version
      fromVersion.value = revisions.value[1].version
      await loadDiff()
    } else {
      fromVersion.value = revisions.value[0]?.version ?? null
      toVersion.value = revisions.value[0]?.version ?? null
    }
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

async function loadDiff(): Promise<void> {
  if (!props.caseData || fromVersion.value === null || toVersion.value === null) return
  diffLoading.value = true
  try {
    diff.value = await api.cases.revisionDiff(props.caseData.id, fromVersion.value, toVersion.value)
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    diffLoading.value = false
  }
}

async function rollback(version: number): Promise<void> {
  if (!props.caseData) return
  if (!window.confirm(`确认回滚到 v${version}？回滚会生成一个新版本，历史记录不会丢失。`)) return
  rolling.value = true
  try {
    await api.cases.rollback(props.caseData.id, version, `回滚至 v${version}`)
    toast.success(`已回滚到 v${version}`)
    emit('updated')
    await load()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    rolling.value = false
  }
}

watch(
  () => [props.modelValue, props.caseData?.id] as const,
  ([open]) => {
    if (open) void load()
  },
  { immediate: true },
)
</script>

<template>
  <BaseModal
    :model-value="modelValue"
    width="xl"
    :title="caseData ? `${caseData.code} · 版本审计` : '版本审计'"
    subtitle="字段级 Diff、修改记录与一键回滚"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div v-if="loading" class="py-10 text-center text-sm text-slate-500">加载版本记录…</div>
    <div v-else-if="revisions.length === 0">
      <EmptyState title="暂无版本记录" description="该用例还没有生成版本快照。" />
    </div>
    <div v-else class="space-y-5">
      <div class="grid gap-3 rounded-xl border border-slate-800 bg-slate-950/50 p-3 sm:grid-cols-[1fr_auto_1fr_auto] sm:items-end">
        <div>
          <label class="label">基线版本</label>
          <select v-model="fromVersion" class="input">
            <option v-for="version in versions" :key="version" :value="version">v{{ version }}</option>
          </select>
        </div>
        <div class="pb-2 text-center text-xs text-slate-500">对比</div>
        <div>
          <label class="label">目标版本</label>
          <select v-model="toVersion" class="input">
            <option v-for="version in versions" :key="version" :value="version">v{{ version }}</option>
          </select>
        </div>
        <button class="btn-primary" :disabled="diffLoading || fromVersion === null || toVersion === null" @click="loadDiff">
          {{ diffLoading ? '对比中…' : '查看 Diff' }}
        </button>
      </div>

      <div v-if="diff" class="overflow-hidden rounded-xl border border-slate-800">
        <div class="flex items-center justify-between border-b border-slate-800 bg-slate-950/60 px-3 py-2">
          <span class="text-xs text-slate-400">
            v{{ diff.from_version }} → v{{ diff.to_version }}，共 {{ diff.total }} 处变更
          </span>
          <span class="text-[11px] text-slate-600">字段级快照对比</span>
        </div>
        <table v-if="diff.changes.length" class="table-shell">
          <thead>
            <tr>
              <th class="th">字段</th>
              <th class="th">变更类型</th>
              <th class="th">旧值</th>
              <th class="th">新值</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="change in diff.changes" :key="change.path">
              <td class="td">
                <p class="text-slate-200">{{ change.label }}</p>
                <p class="font-mono text-[10px] text-slate-500">{{ change.path }}</p>
              </td>
              <td class="td">
                <span
                  class="rounded-md px-1.5 py-0.5 text-[10px]"
                  :class="{
                    'bg-emerald-500/10 text-emerald-300': change.change_type === 'added',
                    'bg-rose-500/10 text-rose-300': change.change_type === 'removed',
                    'bg-amber-500/10 text-amber-300': change.change_type === 'modified',
                  }"
                >
                  {{ change.change_type === 'added' ? '新增' : change.change_type === 'removed' ? '删除' : '修改' }}
                </span>
              </td>
              <td class="td max-w-[240px] break-all text-xs text-slate-500">{{ display(change.old) }}</td>
              <td class="td max-w-[240px] break-all text-xs text-slate-200">{{ display(change.new) }}</td>
            </tr>
          </tbody>
        </table>
        <EmptyState v-else compact title="两个版本完全一致" description="没有检测到字段级差异。" />
      </div>

      <div>
        <h3 class="panel-title mb-2">修改记录</h3>
        <ol class="space-y-2">
          <li
            v-for="revision in revisions"
            :key="revision.id"
            class="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800 bg-slate-950/40 px-3 py-2.5"
          >
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-mono text-xs font-semibold text-indigo-300">v{{ revision.version }}</span>
                <StatusPill :status="revision.change_type" size="sm" />
                <span v-if="revision.version === caseData?.current_version" class="tag">当前版本</span>
              </div>
              <p class="mt-1 text-xs text-slate-300">{{ revision.summary || statusLabel(revision.change_type) }}</p>
              <p class="mt-0.5 text-[10px] text-slate-600">
                {{ revision.operator }} · {{ formatDateTime(revision.created_at) }}
              </p>
            </div>
            <button
              class="btn-ghost btn-xs"
              :disabled="rolling || revision.version === caseData?.current_version"
              @click="rollback(revision.version)"
            >
              回滚到此版本
            </button>
          </li>
        </ol>
      </div>
    </div>
  </BaseModal>
</template>

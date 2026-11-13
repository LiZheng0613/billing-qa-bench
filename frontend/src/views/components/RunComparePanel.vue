<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from '../../api'
import BaseModal from '../../components/BaseModal.vue'
import EmptyState from '../../components/EmptyState.vue'
import JsonViewer from '../../components/JsonViewer.vue'
import StatusPill from '../../components/StatusPill.vue'
import type { CaseExecutionItem, RunCompare, RunCompareSide, TestCase } from '../../types'
import { errorMessage } from '../../utils/errors'
import { formatDateTime, formatScore, safeJson, statusLabel } from '../../utils/format'
import { useToastStore } from '../../stores/toast'

const props = defineProps<{ modelValue: boolean; caseData: TestCase | null }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()

const toast = useToastStore()
const executions = ref<CaseExecutionItem[]>([])
const runA = ref<number | null>(null)
const runB = ref<number | null>(null)
const result = ref<RunCompare | null>(null)
const loading = ref(false)
const comparing = ref(false)

function executionLabel(item: CaseExecutionItem): string {
  return `#${item.id} · v${item.case_version} · ${statusLabel(item.status)} · ${formatScore(item.total_score)} 分`
}

function sideValue(side: RunCompareSide | null, key: 'final_answer' | 'generated_sql' | 'judge_reason'): string {
  if (!side) return '—'
  const value = side[key]
  return value || '—'
}

function formatChangeValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return safeJson(value, 0)
  return String(value)
}

async function loadExecutions(): Promise<void> {
  if (!props.caseData) return
  loading.value = true
  try {
    const page = await api.cases.executions(props.caseData.id, { page_size: 100 })
    executions.value = page.items.filter((item) => item.pass_flag !== null || item.status !== 'RUNNING')
    if (executions.value.length >= 2) {
      runA.value = executions.value[1].id
      runB.value = executions.value[0].id
      await loadCompare()
    } else if (executions.value.length === 1) {
      runA.value = executions.value[0].id
      runB.value = executions.value[0].id
      result.value = null
    } else {
      result.value = null
    }
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
  }
}

async function loadCompare(): Promise<void> {
  if (runA.value === null || runB.value === null) return
  if (runA.value === runB.value) {
    result.value = null
    return
  }
  comparing.value = true
  try {
    result.value = await api.cases.compareRuns(runA.value, runB.value)
  } catch (exc) {
    toast.error(errorMessage(exc))
    result.value = null
  } finally {
    comparing.value = false
  }
}

watch(
  () => [props.modelValue, props.caseData?.id] as const,
  ([open]) => {
    if (open) void loadExecutions()
  },
  { immediate: true },
)

watch([runA, runB], () => {
  if (props.modelValue) void loadCompare()
})
</script>

<template>
  <BaseModal
    :model-value="modelValue"
    width="xl"
    :title="caseData ? `${caseData.code} · 执行对比` : '执行对比'"
    subtitle="选择两次执行，对比版本、状态、最终回复、SQL、底层取数与裁判结论"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div v-if="loading" class="py-10 text-center text-sm text-slate-500">加载执行记录…</div>
    <EmptyState
      v-else-if="executions.length < 2"
      title="可对比的执行不足"
      description="至少需要两次执行记录，先跑一次调试或批量任务。"
    />
    <div v-else class="space-y-5">
      <div class="grid gap-3 rounded-xl border border-slate-800 bg-slate-950/50 p-3 sm:grid-cols-2">
        <div>
          <label class="label">基线执行 A</label>
          <select v-model="runA" class="input">
            <option v-for="item in executions" :key="item.id" :value="item.id">
              {{ executionLabel(item) }}
            </option>
          </select>
        </div>
        <div>
          <label class="label">对比执行 B</label>
          <select v-model="runB" class="input">
            <option v-for="item in executions" :key="item.id" :value="item.id">
              {{ executionLabel(item) }}
            </option>
          </select>
        </div>
      </div>

      <p
        v-if="runA === runB"
        class="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300"
      >
        两次执行相同，请选择不同的执行记录进行对比。
      </p>

      <div v-if="comparing" class="py-8 text-center text-sm text-slate-500">对比中…</div>

      <template v-else-if="result">
        <div class="grid gap-3 lg:grid-cols-2">
          <div
            v-for="(side, index) in [result.left, result.right]"
            :key="side.execution_id"
            class="rounded-xl border border-slate-800 bg-slate-950/40 p-4"
          >
            <div class="flex items-center justify-between">
              <span class="text-xs font-semibold uppercase tracking-wider text-slate-400">
                {{ index === 0 ? '基线 A' : '对比 B' }} · #{{ side.execution_id }}
              </span>
              <StatusPill :status="side.status" size="sm" />
            </div>
            <div class="mt-3 grid grid-cols-3 gap-2 text-center">
              <div class="rounded-lg bg-slate-900/70 px-2 py-1.5">
                <p class="text-[10px] text-slate-500">版本</p>
                <p class="text-sm font-semibold text-slate-200">v{{ side.case_version }}</p>
              </div>
              <div class="rounded-lg bg-slate-900/70 px-2 py-1.5">
                <p class="text-[10px] text-slate-500">得分</p>
                <p class="text-sm font-semibold text-slate-200">{{ formatScore(side.total_score) }}</p>
              </div>
              <div class="rounded-lg bg-slate-900/70 px-2 py-1.5">
                <p class="text-[10px] text-slate-500">开始</p>
                <p class="text-[11px] text-slate-300">{{ formatDateTime(side.started_at).slice(11) }}</p>
              </div>
            </div>
            <dl class="mt-3 space-y-2 text-xs">
              <div>
                <dt class="text-slate-500">最终回复</dt>
                <dd class="mt-0.5 whitespace-pre-wrap break-words rounded-lg bg-slate-900/60 p-2 text-slate-300">
                  {{ sideValue(side, 'final_answer') }}
                </dd>
              </div>
              <div>
                <dt class="text-slate-500">生成 SQL</dt>
                <dd class="mono mt-0.5 break-all rounded-lg bg-slate-900/60 p-2 text-slate-400">
                  {{ sideValue(side, 'generated_sql') }}
                </dd>
              </div>
              <div>
                <dt class="text-slate-500">裁判结论</dt>
                <dd class="mt-0.5 whitespace-pre-wrap break-words rounded-lg bg-slate-900/60 p-2 text-slate-400">
                  {{ sideValue(side, 'judge_reason') }}
                </dd>
              </div>
            </dl>
            <div class="mt-3">
              <JsonViewer label="底线取数 raw_db_data" :value="side.raw_db_data" max-height="160px" />
            </div>
            <details class="mt-3 rounded-lg border border-slate-800 bg-slate-900/40 p-2">
              <summary class="cursor-pointer text-[11px] text-slate-400">
                轮次明细（{{ side.turns.length }} 轮）
              </summary>
              <div class="mt-2 space-y-2">
                <div
                  v-for="turn in side.turns"
                  :key="String(turn.turn_index)"
                  class="rounded-lg bg-slate-950/60 p-2 text-[11px]"
                >
                  <p class="text-slate-400">
                    T{{ turn.turn_index }} · {{ turn.query }}
                  </p>
                  <p class="mt-1 text-slate-300">{{ turn.final_answer || '—' }}</p>
                </div>
              </div>
            </details>
          </div>
        </div>

        <div>
          <h3 class="panel-title mb-2">关键差异（{{ result.diffs.length }}）</h3>
          <div class="overflow-hidden rounded-xl border border-slate-800">
            <table v-if="result.diffs.length" class="table-shell">
              <thead>
                <tr>
                  <th class="th">字段</th>
                  <th class="th">基线 A</th>
                  <th class="th">对比 B</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="change in result.diffs" :key="change.path">
                  <td class="td">
                    <p class="text-slate-200">{{ change.label }}</p>
                    <p class="font-mono text-[10px] text-slate-500">{{ change.path }}</p>
                  </td>
                  <td class="td max-w-[260px] break-all text-xs text-slate-500">
                    {{ formatChangeValue(change.old) }}
                  </td>
                  <td class="td max-w-[260px] break-all text-xs text-emerald-200">
                    {{ formatChangeValue(change.new) }}
                  </td>
                </tr>
              </tbody>
            </table>
            <EmptyState v-else compact title="两次执行结果一致" description="没有检测到关键字段差异。" />
          </div>
        </div>
      </template>
    </div>
  </BaseModal>
</template>

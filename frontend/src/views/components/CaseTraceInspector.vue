<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import EmptyState from '../../components/EmptyState.vue'
import JsonViewer from '../../components/JsonViewer.vue'
import StatusPill from '../../components/StatusPill.vue'
import type { ExecutionDetail, JudgeResult, TurnTrace } from '../../types'
import {
  formatDateTime,
  formatMs,
  formatNumber,
  formatScore,
  safeJson,
  sourceLabel,
  statusLabel,
} from '../../utils/format'
import { useToastStore } from '../../stores/toast'

const props = defineProps<{ detail: ExecutionDetail | null; loading: boolean }>()
const emit = defineEmits<{ (e: 'refresh'): void }>()

const toast = useToastStore()

type TabKey = 'turns' | 'judges' | 'rules' | 'snapshot' | 'raw'
const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'turns', label: '对话轮次' },
  { key: 'judges', label: '裁判明细' },
  { key: 'rules', label: '规则校验' },
  { key: 'snapshot', label: '用例快照' },
  { key: 'raw', label: '原始报文' },
]
const activeTab = ref<TabKey>('turns')

interface RuleCheckItem {
  type?: string
  label?: string
  expected?: unknown
  actual?: unknown
  passed?: boolean
  evidence?: string
}

const DIMENSION_MAX: Record<string, number> = {
  data_accuracy: 40,
  logic_consistency: 30,
  goal_completion: 20,
  tone_clarity: 10,
}

const DIMENSION_LABEL: Record<string, string> = {
  data_accuracy: '数据准确性',
  logic_consistency: '逻辑一致性',
  goal_completion: '目标完成度',
  tone_clarity: '语气清晰度',
}

const ruleBlock = computed(() => {
  const raw = props.detail?.rule_result
  if (!raw || typeof raw !== 'object') return null
  return raw as { passed?: boolean; checks?: RuleCheckItem[]; extracted?: Record<string, unknown> }
})

const ruleChecks = computed<RuleCheckItem[]>(() => ruleBlock.value?.checks ?? [])

const aggregate = computed(() => {
  const raw = props.detail?.judge_result
  if (!raw || typeof raw !== 'object') return null
  return raw as Record<string, unknown>
})

const policyNotes = computed<string[]>(() => {
  const notes = aggregate.value?.policy_notes
  return Array.isArray(notes) ? notes.map(String) : []
})

const dims = computed(() => {
  const raw = aggregate.value?.dimension_scores
  if (!raw || typeof raw !== 'object') return [] as Array<{ key: string; label: string; value: number }>
  return Object.entries(raw as Record<string, number>).map(([key, value]) => ({
    key,
    label: DIMENSION_LABEL[key] ?? key,
    value,
  }))
})

watch(
  () => props.detail?.id,
  () => {
    activeTab.value = 'turns'
  },
)

function turnTone(turn: TurnTrace): string {
  if (turn.input_source === 'SIMULATOR') return 'border-fuchsia-500/40 text-fuchsia-300'
  if (turn.input_source === 'STATIC') return 'border-sky-500/40 text-sky-300'
  return 'border-indigo-500/40 text-indigo-300'
}

function ruleCheckLabel(check: RuleCheckItem): string {
  if (check.label) return check.label
  if (check.type === 'must_contain') return '必须包含'
  if (check.type === 'must_not_contain') return '禁止包含'
  return check.type ?? '校验项'
}

function valueText(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return safeJson(value, 0)
  return String(value)
}

function judgeDimensions(judge: JudgeResult): Array<{ key: string; label: string; value: number; max: number }> {
  return Object.entries(judge.dimension_scores ?? {}).map(([key, value]) => ({
    key,
    label: DIMENSION_LABEL[key] ?? key,
    value,
    max: DIMENSION_MAX[key] ?? 10,
  }))
}

async function copyTraceUid(): Promise<void> {
  if (!props.detail) return
  try {
    await navigator.clipboard.writeText(props.detail.trace_uid)
    toast.success('Trace UID 已复制')
  } catch {
    toast.error('复制失败')
  }
}
</script>

<template>
  <section class="card flex min-h-[520px] flex-col overflow-hidden">
    <div v-if="loading" class="py-24 text-center text-sm text-slate-500">加载执行详情…</div>
    <EmptyState
      v-else-if="!detail"
      title="选择一条执行记录"
      description="左侧列表支持按状态与来源筛选，点击任意记录查看全链路 Trace。"
    />
    <template v-else>
      <header class="border-b border-slate-800 px-5 py-4">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h2 class="truncate text-base font-semibold text-slate-100">
                {{ detail.case_code || `用例 #${detail.case_id}` }}
              </h2>
              <StatusPill :status="detail.status" />
              <span class="tag">v{{ detail.case_version }}</span>
              <span class="tag">{{ sourceLabel(detail.source) }}</span>
            </div>
            <p class="mt-1 truncate text-xs text-slate-500">{{ detail.case_name || '—' }}</p>
            <button
              class="mt-1 inline-flex items-center gap-1 font-mono text-[10px] text-slate-600 transition hover:text-slate-300"
              @click="copyTraceUid"
            >
              trace: {{ detail.trace_uid }}
              <svg class="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <path stroke-linecap="round" stroke-linejoin="round" d="M8 8V5a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-3M5 8h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2Z" />
              </svg>
            </button>
          </div>
          <button class="btn-ghost btn-xs" @click="emit('refresh')">刷新</button>
        </div>

        <div class="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-7">
          <div>
            <p class="text-[10px] uppercase tracking-wider text-slate-500">总分</p>
            <p class="text-lg font-semibold tabular-nums text-indigo-300">{{ formatScore(detail.total_score) }}</p>
          </div>
          <div>
            <p class="text-[10px] uppercase tracking-wider text-slate-500">轮次</p>
            <p class="text-lg font-semibold tabular-nums text-slate-100">{{ detail.turns_used }}</p>
          </div>
          <div>
            <p class="text-[10px] uppercase tracking-wider text-slate-500">总时延</p>
            <p class="text-sm font-semibold tabular-nums text-slate-200">{{ formatMs(detail.latency_ms) }}</p>
          </div>
          <div>
            <p class="text-[10px] uppercase tracking-wider text-slate-500">首字时延</p>
            <p class="text-sm font-semibold tabular-nums text-slate-200">{{ formatMs(detail.ttft_ms) }}</p>
          </div>
          <div>
            <p class="text-[10px] uppercase tracking-wider text-slate-500">Tokens</p>
            <p class="text-sm font-semibold tabular-nums text-slate-200">
              {{ formatNumber(detail.simulator_tokens + detail.judge_tokens) }}
            </p>
          </div>
          <div>
            <p class="text-[10px] uppercase tracking-wider text-slate-500">终止状态</p>
            <p class="text-xs font-medium text-slate-300">{{ statusLabel(detail.termination_status) }}</p>
          </div>
          <div>
            <p class="text-[10px] uppercase tracking-wider text-slate-500">开始时间</p>
            <p class="text-[11px] text-slate-300">{{ formatDateTime(detail.started_at) }}</p>
          </div>
        </div>

        <p
          v-if="detail.error_message"
          class="mt-3 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300"
        >
          {{ detail.error_message }}
        </p>
        <div v-if="policyNotes.length" class="mt-3 space-y-1 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2">
          <p v-for="note in policyNotes" :key="note" class="text-xs text-amber-300">· {{ note }}</p>
        </div>
      </header>

      <div class="flex flex-wrap gap-1 border-b border-slate-800 px-4 py-2">
        <button
          v-for="tab in TABS"
          :key="tab.key"
          class="tab-btn"
          :class="activeTab === tab.key ? 'tab-btn-active' : ''"
          @click="activeTab = tab.key"
        >
          {{ tab.label }}
          <span v-if="tab.key === 'turns'" class="ml-1 text-[10px] text-slate-500">{{ detail.turns.length }}</span>
          <span v-if="tab.key === 'judges'" class="ml-1 text-[10px] text-slate-500">{{ detail.judges.length }}</span>
        </button>
      </div>

      <div class="flex-1 overflow-y-auto px-4 py-4">
        <template v-if="activeTab === 'turns'">
          <div v-if="detail.turns.length === 0">
            <EmptyState compact title="没有对话轮次" description="该执行未产生有效对话。" />
          </div>
          <div v-else class="space-y-3">
            <article
              v-for="turn in detail.turns"
              :key="turn.id"
              class="rounded-xl border border-slate-800 bg-slate-950/40"
            >
              <header class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/70 px-3 py-2">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="font-mono text-xs font-semibold text-indigo-300">T{{ turn.turn_index }}</span>
                  <span class="rounded-full border px-2 py-0.5 text-[10px]" :class="turnTone(turn)">
                    {{ sourceLabel(turn.input_source) }}
                  </span>
                  <span v-if="turn.simulator_status" class="tag">{{ statusLabel(turn.simulator_status) }}</span>
                  <span v-if="turn.intent" class="tag">意图：{{ turn.intent }}</span>
                </div>
                <div class="flex items-center gap-3 text-[10px] text-slate-500">
                  <span>TTFT {{ formatMs(turn.ttft_ms) }}</span>
                  <span>时延 {{ formatMs(turn.latency_ms) }}</span>
                  <span>
                    Tokens {{ formatNumber(turn.simulator_prompt_tokens + turn.simulator_completion_tokens) }}
                  </span>
                </div>
              </header>

              <div class="grid gap-3 px-3 py-3 lg:grid-cols-2">
                <div>
                  <p class="text-[10px] uppercase tracking-wider text-slate-500">用户提问</p>
                  <p class="mt-1 whitespace-pre-wrap break-words rounded-lg bg-slate-900/70 p-2 text-sm text-slate-200">
                    {{ turn.query }}
                  </p>
                  <div v-if="turn.simulator_thought" class="mt-2 rounded-lg border border-fuchsia-500/20 bg-fuchsia-500/5 p-2">
                    <p class="text-[10px] uppercase tracking-wider text-fuchsia-400/80">模拟器思考</p>
                    <p class="mt-1 whitespace-pre-wrap break-words text-xs text-slate-400">{{ turn.simulator_thought }}</p>
                  </div>
                  <div v-if="turn.hidden_context_released?.length" class="mt-2">
                    <p class="text-[10px] uppercase tracking-wider text-slate-500">本轮释放隐藏上下文</p>
                    <pre class="mono mt-1 rounded-lg bg-slate-900/70 p-2 text-slate-400">{{ safeJson(turn.hidden_context_released) }}</pre>
                  </div>
                </div>
                <div>
                  <p class="text-[10px] uppercase tracking-wider text-slate-500">客服最终回复</p>
                  <p class="mt-1 whitespace-pre-wrap break-words rounded-lg bg-slate-900/70 p-2 text-sm text-slate-200">
                    {{ turn.final_answer || '—' }}
                  </p>
                  <div v-if="turn.generated_sql" class="mt-2">
                    <p class="text-[10px] uppercase tracking-wider text-slate-500">生成 SQL</p>
                    <pre class="mono mt-1 overflow-auto rounded-lg bg-slate-900/70 p-2 text-sky-300">{{ turn.generated_sql }}</pre>
                  </div>
                  <div v-if="turn.api_params" class="mt-2">
                    <JsonViewer :value="turn.api_params" label="API 参数" max-height="120px" />
                  </div>
                </div>
              </div>

              <div v-if="turn.raw_db_data" class="border-t border-slate-800/70 px-3 py-2">
                <JsonViewer :value="turn.raw_db_data" label="底层取数 raw_db_data" max-height="160px" />
              </div>

              <details v-if="turn.rule_check" class="border-t border-slate-800/70 px-3 py-2">
                <summary class="cursor-pointer text-[11px] text-slate-500 hover:text-slate-300">
                  本轮规则校验
                  <span :class="turn.rule_check.passed ? 'text-emerald-400' : 'text-rose-400'">
                    {{ turn.rule_check.passed ? '通过' : '未通过' }}
                  </span>
                </summary>
                <pre class="mono mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-slate-400">{{ safeJson(turn.rule_check) }}</pre>
              </details>

              <details class="border-t border-slate-800/70 px-3 py-2">
                <summary class="cursor-pointer text-[11px] text-slate-500 hover:text-slate-300">
                  请求 / 响应原始报文
                </summary>
                <div class="mt-2 grid gap-2 lg:grid-cols-2">
                  <JsonViewer :value="turn.request_payload" label="请求" max-height="220px" />
                  <JsonViewer :value="turn.response_raw" label="响应" max-height="220px" />
                </div>
              </details>

              <details v-if="turn.rendered_prompt" class="border-t border-slate-800/70 px-3 py-2">
                <summary class="cursor-pointer text-[11px] text-slate-500 hover:text-slate-300">
                  模拟器渲染 Prompt
                </summary>
                <pre class="mono mt-2 max-h-52 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-900/70 p-2 text-slate-400">{{ turn.rendered_prompt }}</pre>
              </details>
            </article>
          </div>
        </template>

        <template v-else-if="activeTab === 'judges'">
          <div class="mb-3 rounded-xl border border-slate-800 bg-slate-950/40 px-3 py-2">
            <div class="flex flex-wrap items-center gap-3 text-xs text-slate-400">
              <span>裁判策略：{{ statusLabel(String(aggregate?.strategy ?? '—')) }}</span>
              <span>裁判数量：{{ String(aggregate?.judge_count ?? detail.judges.length) }}</span>
              <span>综合得分：<span class="text-slate-100">{{ formatScore(detail.total_score) }}</span></span>
              <span>
                综合结论：
                <span :class="aggregate?.pass ? 'text-emerald-400' : 'text-rose-400'">
                  {{ aggregate?.pass ? '通过' : '未通过' }}
                </span>
              </span>
            </div>
            <div v-if="dims.length" class="mt-2 flex flex-wrap gap-3">
              <span v-for="item in dims" :key="item.key" class="text-[11px] text-slate-500">
                {{ item.label }}
                <span class="text-slate-300">{{ item.value }}</span>
              </span>
            </div>
          </div>

          <div v-if="detail.judges.length === 0">
            <EmptyState compact title="没有裁判明细" description="该执行未运行 LLM 裁判。" />
          </div>
          <div v-else class="space-y-3">
            <article
              v-for="judge in detail.judges"
              :key="judge.id"
              class="rounded-xl border border-slate-800 bg-slate-950/40 p-3"
            >
              <header class="flex flex-wrap items-center justify-between gap-2">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="text-sm font-medium text-slate-200">{{ judge.model_name }}</span>
                  <StatusPill :status="judge.pass_flag ? 'PASS' : 'FAIL'" size="sm" />
                  <span class="tag">得分 {{ formatScore(judge.total_score) }}</span>
                </div>
                <span class="text-[10px] text-slate-500">
                  Tokens {{ judge.prompt_tokens }} / {{ judge.completion_tokens }}
                </span>
              </header>

              <div v-if="judgeDimensions(judge).length" class="mt-3 grid gap-2 sm:grid-cols-2">
                <div v-for="dim in judgeDimensions(judge)" :key="dim.key">
                  <div class="flex items-center justify-between text-[11px]">
                    <span class="text-slate-500">{{ dim.label }}</span>
                    <span class="text-slate-300">{{ dim.value }} / {{ dim.max }}</span>
                  </div>
                  <div class="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-800">
                    <div
                      class="h-full rounded-full"
                      :class="dim.value >= dim.max * 0.6 ? 'bg-emerald-500' : 'bg-rose-500'"
                      :style="{ width: `${Math.min(100, (dim.value / dim.max) * 100)}%` }"
                    />
                  </div>
                </div>
              </div>

              <div v-if="judge.error_tags?.length" class="mt-2 flex flex-wrap gap-1">
                <span
                  v-for="tag in judge.error_tags"
                  :key="tag"
                  class="rounded-md border border-rose-500/30 bg-rose-500/10 px-1.5 py-0.5 text-[10px] text-rose-300"
                >
                  {{ tag }}
                </span>
              </div>

              <div class="mt-2">
                <p class="text-[10px] uppercase tracking-wider text-slate-500">裁判理由</p>
                <p class="mt-1 whitespace-pre-wrap break-words rounded-lg bg-slate-900/70 p-2 text-xs text-slate-300">
                  {{ judge.reason || '—' }}
                </p>
              </div>

              <details v-if="judge.raw_response" class="mt-2">
                <summary class="cursor-pointer text-[11px] text-slate-500 hover:text-slate-300">结构化裁判响应</summary>
                <div class="mt-2">
                  <JsonViewer :value="judge.raw_response" max-height="200px" />
                </div>
              </details>
              <details v-if="judge.judge_prompt" class="mt-2">
                <summary class="cursor-pointer text-[11px] text-slate-500 hover:text-slate-300">实际裁判 Prompt</summary>
                <pre class="mono mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-900/70 p-2 text-slate-400">{{ judge.judge_prompt }}</pre>
              </details>
            </article>
          </div>
        </template>

        <template v-else-if="activeTab === 'rules'">
          <div v-if="ruleChecks.length" class="space-y-3">
            <div class="flex flex-wrap items-center gap-2">
              <StatusPill :status="ruleBlock?.passed ? 'PASS' : 'FAIL'" />
              <span class="text-xs text-slate-500">共 {{ ruleChecks.length }} 项硬校验</span>
            </div>
            <div class="overflow-hidden rounded-xl border border-slate-800">
              <table class="table-shell">
                <thead>
                  <tr>
                    <th class="th">校验项</th>
                    <th class="th">期望</th>
                    <th class="th">实际</th>
                    <th class="th">证据</th>
                    <th class="th">结果</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(check, index) in ruleChecks" :key="index">
                    <td class="td">
                      <p class="text-slate-200">{{ ruleCheckLabel(check) }}</p>
                      <p class="font-mono text-[10px] text-slate-500">{{ check.type }}</p>
                    </td>
                    <td class="td max-w-[200px] break-all text-xs">{{ valueText(check.expected) }}</td>
                    <td class="td max-w-[220px] break-all text-xs">{{ valueText(check.actual) }}</td>
                    <td class="td max-w-[220px] break-words text-[11px] text-slate-500">{{ check.evidence || '—' }}</td>
                    <td class="td">
                      <StatusPill :status="check.passed ? 'PASS' : 'FAIL'" size="sm" />
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <JsonViewer :value="ruleBlock?.extracted" label="抽取结果 extracted" />
          </div>
          <EmptyState
            v-else
            title="没有规则硬校验"
            description="该用例未配置金额 / 笔数 / 日期 / 关键词规则。"
          />
        </template>

        <template v-else-if="activeTab === 'snapshot'">
          <div class="space-y-3">
            <div>
              <p class="label">执行时的用例快照（case_version v{{ detail.case_version }}）</p>
              <JsonViewer :value="detail.case_snapshot" max-height="420px" label="case_snapshot" />
            </div>
            <div class="grid gap-3 lg:grid-cols-2">
              <JsonViewer :value="detail.rule_result" label="rule_result" max-height="300px" />
              <JsonViewer :value="detail.judge_result" label="judge_result" max-height="300px" />
            </div>
          </div>
        </template>

        <template v-else>
          <div class="space-y-3">
            <div
              v-for="item in detail.raw_request_response"
              :key="String(item.turn)"
              class="grid gap-3 rounded-xl border border-slate-800 bg-slate-950/40 p-3 lg:grid-cols-2"
            >
              <div class="lg:col-span-2">
                <span class="font-mono text-xs font-semibold text-indigo-300">T{{ item.turn }}</span>
              </div>
              <JsonViewer :value="item.request" label="请求" max-height="260px" />
              <JsonViewer :value="item.response" label="响应" max-height="260px" />
            </div>
            <EmptyState v-if="detail.raw_request_response.length === 0" compact title="没有原始报文" />
          </div>
        </template>
      </div>
    </template>
  </section>
</template>

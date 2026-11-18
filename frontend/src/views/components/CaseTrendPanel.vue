<script setup lang="ts">
import { computed, ref, shallowRef, watch } from 'vue'
import type { EChartsOption } from 'echarts'
import { api } from '../../api'
import BaseModal from '../../components/BaseModal.vue'
import EmptyState from '../../components/EmptyState.vue'
import StatusPill from '../../components/StatusPill.vue'
import { useEChart } from '../../composables/useEChart'
import type { CaseExecutionItem, CaseTrend, TestCase } from '../../types'
import { errorMessage } from '../../utils/errors'
import {
  formatDateTime,
  formatPercent,
  formatScore,
  formatSeconds,
  formatTime,
} from '../../utils/format'
import { useToastStore } from '../../stores/toast'

const props = defineProps<{ modelValue: boolean; caseData: TestCase | null }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'select-execution', executionId: number): void
  (e: 'view-all'): void
}>()

const toast = useToastStore()
const trend = ref<CaseTrend | null>(null)
const executions = ref<CaseExecutionItem[]>([])
const loading = ref(false)
const chartEl = ref<HTMLElement | null>(null)
const chartOption = shallowRef<EChartsOption | null>(null)

useEChart(chartEl, chartOption)

const stats = computed(() => [
  { label: '通过率', value: formatPercent(trend.value?.pass_rate, 1), tone: 'text-emerald-700' },
  { label: '平均分', value: formatScore(trend.value?.avg_score), tone: 'text-indigo-600' },
  { label: '平均时延', value: formatSeconds(msToSeconds(trend.value?.avg_latency_ms)), tone: 'text-sky-700' },
  {
    label: '稳定性',
    value: trend.value?.flaky ? '波动 / Flaky' : '稳定',
    tone: trend.value?.flaky ? 'text-amber-700' : 'text-emerald-700',
  },
])

/** 后端时延字段统一为毫秒，趋势视图内全部换算成秒展示。 */
function msToSeconds(ms?: number | null): number | null {
  if (ms === null || ms === undefined) return null
  return Math.round((ms / 1000) * 100) / 100
}

function buildOption(): void {
  const points = trend.value?.points ?? []
  if (points.length === 0) {
    chartOption.value = null
    return
  }
  chartOption.value = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(255, 255, 255, 0.98)',
      borderColor: '#e8e8ed',
      textStyle: { color: '#111827', fontSize: 12 },
      extraCssText: 'box-shadow: 0 10px 30px -18px rgba(17,24,39,.45); border-radius: 10px;',
    },
    legend: {
      data: ['总分', '时延'],
      textStyle: { color: '#6b7280', fontSize: 11 },
      top: 0,
    },
    grid: { left: 44, right: 52, top: 32, bottom: 28 },
    xAxis: {
      type: 'category',
      data: points.map((point) => `#${point.execution_id}\n${formatTime(point.started_at)}`),
      axisLine: { lineStyle: { color: '#e8e8ed' } },
      axisLabel: { color: '#6b7280', fontSize: 10 },
      axisTick: { show: false },
    },
    yAxis: [
      {
        type: 'value',
        min: 0,
        max: 100,
        name: '分',
        nameTextStyle: { color: '#6b7280', fontSize: 10 },
        splitLine: { lineStyle: { color: '#f0f0f4' } },
        axisLabel: { color: '#6b7280', fontSize: 10 },
      },
      {
        type: 'value',
        name: 's',
        nameTextStyle: { color: '#6b7280', fontSize: 10 },
        splitLine: { show: false },
        axisLabel: { color: '#6b7280', fontSize: 10 },
      },
    ],
    series: [
      {
        name: '总分',
        type: 'line',
        smooth: true,
        symbolSize: 7,
        data: points.map((point) => point.total_score),
        itemStyle: { color: '#4f46e5' },
        lineStyle: { width: 2, color: '#4f46e5' },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(79, 70, 229, 0.18)' },
              { offset: 1, color: 'rgba(79, 70, 229, 0.01)' },
            ],
          },
        },
      },
      {
        name: '时延',
        type: 'line',
        smooth: true,
        yAxisIndex: 1,
        symbolSize: 5,
        data: points.map((point) => msToSeconds(point.latency_ms)),
        itemStyle: { color: '#0ea5e9' },
        lineStyle: { width: 1.5, type: 'dashed', color: '#0ea5e9' },
        tooltip: { valueFormatter: (value) => `${value} s` },
      },
    ],
  }
}

async function load(): Promise<void> {
  if (!props.caseData) return
  loading.value = true
  try {
    const [trendData, executionPage] = await Promise.all([
      api.cases.trend(props.caseData.id),
      api.cases.executions(props.caseData.id, { page_size: 20 }),
    ])
    trend.value = trendData
    executions.value = executionPage.items
    buildOption()
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    loading.value = false
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
    :title="caseData ? `${caseData.code} · 历史趋势` : '历史趋势'"
    subtitle="最近 20 次执行得分、时延与稳定性"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div v-if="loading" class="py-10 text-center text-sm text-slate-500">加载执行趋势…</div>
    <EmptyState
      v-else-if="!trend || trend.points.length === 0"
      title="暂无历史执行"
      description="先对用例执行一次调试或批量任务，即可看到趋势。"
    />
    <div v-else class="space-y-5">
      <div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div v-for="item in stats" :key="item.label" class="rounded-xl border border-slate-200 bg-slate-50/80 px-3 py-2.5">
          <p class="text-[10px] uppercase tracking-wider text-slate-500">{{ item.label }}</p>
          <p class="mt-0.5 text-lg font-semibold tabular-nums" :class="item.tone">{{ item.value }}</p>
        </div>
      </div>

      <div class="rounded-xl border border-slate-200 bg-slate-50 p-3">
        <div ref="chartEl" class="h-64 w-full" />
      </div>

      <div>
        <div class="mb-2 flex items-center justify-between">
          <h3 class="panel-title">最近执行记录</h3>
          <button class="btn-ghost btn-xs" @click="emit('view-all')">查看全部历史</button>
        </div>
        <div class="overflow-x-auto rounded-xl border border-slate-200">
          <table class="table-shell">
            <thead>
              <tr>
                <th class="th">#</th>
                <th class="th">状态</th>
                <th class="th">得分</th>
                <th class="th">时延</th>
                <th class="th">轮次</th>
                <th class="th">开始时间</th>
                <th class="th"></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in executions" :key="item.id">
                <td class="td">
                  <button
                    type="button"
                    class="font-mono text-xs text-indigo-600 transition hover:text-indigo-800 hover:underline"
                    title="查看执行详情"
                    @click="emit('select-execution', item.id)"
                  >
                    #{{ item.id }}
                  </button>
                </td>
                <td class="td"><StatusPill :status="item.status" size="sm" /></td>
                <td class="td tabular-nums">{{ formatScore(item.total_score) }}</td>
                <td class="td tabular-nums">{{ formatSeconds(msToSeconds(item.latency_ms)) }}</td>
                <td class="td tabular-nums">{{ item.turns_used }}</td>
                <td class="td text-xs text-slate-500">{{ formatDateTime(item.started_at) }}</td>
                <td class="td text-right">
                  <button class="btn-ghost btn-xs" @click="emit('select-execution', item.id)">查看 Trace</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </BaseModal>
</template>

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
  formatMs,
  formatPercent,
  formatScore,
  formatTime,
} from '../../utils/format'
import { useToastStore } from '../../stores/toast'

const props = defineProps<{ modelValue: boolean; caseData: TestCase | null }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'select-execution', executionId: number): void
}>()

const toast = useToastStore()
const trend = ref<CaseTrend | null>(null)
const executions = ref<CaseExecutionItem[]>([])
const loading = ref(false)
const chartEl = ref<HTMLElement | null>(null)
const chartOption = shallowRef<EChartsOption | null>(null)

useEChart(chartEl, chartOption)

const stats = computed(() => [
  { label: '通过率', value: formatPercent(trend.value?.pass_rate, 1), tone: 'text-emerald-300' },
  { label: '平均分', value: formatScore(trend.value?.avg_score), tone: 'text-indigo-300' },
  { label: '平均时延', value: formatMs(trend.value?.avg_latency_ms), tone: 'text-sky-300' },
  {
    label: '稳定性',
    value: trend.value?.flaky ? '波动 / Flaky' : '稳定',
    tone: trend.value?.flaky ? 'text-amber-300' : 'text-emerald-300',
  },
])

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
      backgroundColor: 'rgba(15, 23, 42, 0.95)',
      borderColor: '#334155',
      textStyle: { color: '#e2e8f0', fontSize: 12 },
    },
    legend: {
      data: ['总分', '时延'],
      textStyle: { color: '#94a3b8', fontSize: 11 },
      top: 0,
    },
    grid: { left: 44, right: 52, top: 32, bottom: 28 },
    xAxis: {
      type: 'category',
      data: points.map((point) => `#${point.execution_id}\n${formatTime(point.started_at)}`),
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#64748b', fontSize: 10 },
      axisTick: { show: false },
    },
    yAxis: [
      {
        type: 'value',
        min: 0,
        max: 100,
        name: '分',
        nameTextStyle: { color: '#64748b', fontSize: 10 },
        splitLine: { lineStyle: { color: 'rgba(51, 65, 85, 0.5)' } },
        axisLabel: { color: '#64748b', fontSize: 10 },
      },
      {
        type: 'value',
        name: 'ms',
        nameTextStyle: { color: '#64748b', fontSize: 10 },
        splitLine: { show: false },
        axisLabel: { color: '#64748b', fontSize: 10 },
      },
    ],
    series: [
      {
        name: '总分',
        type: 'line',
        smooth: true,
        symbolSize: 7,
        data: points.map((point) => point.total_score),
        itemStyle: { color: '#818cf8' },
        lineStyle: { width: 2, color: '#818cf8' },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(129, 140, 248, 0.28)' },
              { offset: 1, color: 'rgba(129, 140, 248, 0.02)' },
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
        data: points.map((point) => point.latency_ms),
        itemStyle: { color: '#22d3ee' },
        lineStyle: { width: 1.5, type: 'dashed', color: '#22d3ee' },
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
        <div v-for="item in stats" :key="item.label" class="rounded-xl border border-slate-800 bg-slate-950/50 px-3 py-2.5">
          <p class="text-[10px] uppercase tracking-wider text-slate-500">{{ item.label }}</p>
          <p class="mt-0.5 text-lg font-semibold tabular-nums" :class="item.tone">{{ item.value }}</p>
        </div>
      </div>

      <div class="rounded-xl border border-slate-800 bg-slate-950/40 p-3">
        <div ref="chartEl" class="h-64 w-full" />
      </div>

      <div>
        <h3 class="panel-title mb-2">最近执行记录</h3>
        <div class="overflow-x-auto rounded-xl border border-slate-800">
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
                <td class="td font-mono text-xs text-slate-500">{{ item.id }}</td>
                <td class="td"><StatusPill :status="item.status" size="sm" /></td>
                <td class="td tabular-nums">{{ formatScore(item.total_score) }}</td>
                <td class="td tabular-nums">{{ formatMs(item.latency_ms) }}</td>
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

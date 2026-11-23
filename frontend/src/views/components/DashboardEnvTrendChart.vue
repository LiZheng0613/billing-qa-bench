<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ExecutionEnvironmentTrendSeries } from '../../types'

const props = defineProps<{ series: ExecutionEnvironmentTrendSeries[] }>()

const WIDTH = 762
const HEIGHT = 232
const LEFT = 44
const RIGHT = 704
const TOP = 18
const BOTTOM = 196

const hovered = ref<number | null>(null)

const dates = computed(() => props.series[0]?.points.map((item) => item.date) ?? [])

const rates = computed(() =>
  props.series.flatMap((item) =>
    item.points.map((point) => point.pass_rate).filter((value): value is number => value !== null),
  ),
)

const minRate = computed(() => {
  if (!rates.value.length) return 80
  const floor = Math.floor(Math.min(...rates.value) / 5) * 5
  return Math.max(0, Math.min(80, floor))
})

const step = computed(() =>
  dates.value.length > 1 ? (RIGHT - LEFT) / (dates.value.length - 1) : 0,
)

const gridValues = computed(() => {
  const min = minRate.value
  const mid = Math.round((min + 100) / 2 / 5) * 5
  return [100, mid, min]
})

const labelEvery = computed(() => Math.max(1, Math.ceil(dates.value.length / 9)))

function x(index: number): number {
  return LEFT + step.value * index
}

function y(rate: number): number {
  const span = 100 - minRate.value || 1
  return BOTTOM - ((rate - minRate.value) / span) * (BOTTOM - TOP)
}

function seriesPath(points: ExecutionEnvironmentTrendSeries['points']): string {
  let path = ''
  let drawing = false
  points.forEach((point, index) => {
    if (point.pass_rate === null) {
      drawing = false
      return
    }
    const command = drawing ? 'L' : 'M'
    path += `${command}${x(index).toFixed(1)},${y(point.pass_rate).toFixed(1)} `
    drawing = true
  })
  return path.trim()
}

function dots(points: ExecutionEnvironmentTrendSeries['points']) {
  return points
    .map((point, index) => ({ point, index }))
    .filter((entry) => entry.point.pass_rate !== null)
}

function dateLabel(value: string): string {
  return value.slice(5)
}

function rateLabel(value: number | null): string {
  return value === null ? '未判定' : `${value.toFixed(1)}%`
}

const activeIndex = computed(() => {
  if (hovered.value === null) return null
  return dates.value[hovered.value] ? hovered.value : null
})

const tooltip = computed(() => {
  if (activeIndex.value === null) return null
  const index = activeIndex.value
  const width = 178
  const rowHeight = 15
  const height = 30 + props.series.length * rowHeight
  const boxX = Math.min(RIGHT - width, Math.max(LEFT, x(index) - width / 2))
  return {
    width,
    height,
    rowHeight,
    x: boxX,
    y: TOP + 8,
    date: dates.value[index],
    rows: props.series.map((item) => ({
      key: item.key,
      name: item.name,
      color: item.color,
      rate: rateLabel(item.points[index]?.pass_rate ?? null),
      measured: item.points[index]?.measured ?? 0,
    })),
  }
})
</script>

<template>
  <div>
    <div class="flex flex-wrap items-center gap-x-4 gap-y-1.5 pb-2 text-[10px] text-ink-muted">
      <span v-for="item in series" :key="item.key" class="inline-flex min-w-0 items-center gap-1.5">
        <i class="h-[3px] w-4 flex-none rounded-full" :style="{ backgroundColor: item.color }" />
        <span class="max-w-[220px] break-words leading-snug">{{ item.name }}</span>
      </span>
    </div>

    <svg
      class="block w-full"
      :viewBox="`0 0 ${WIDTH} ${HEIGHT}`"
      role="img"
      aria-label="各执行环境每日通过率对比"
    >
      <g v-for="value in gridValues" :key="value">
        <line
          :x1="LEFT"
          :y1="y(value)"
          :x2="RIGHT"
          :y2="y(value)"
          :stroke="value === minRate ? '#e8e8ed' : '#f0f0f4'"
        />
        <text :x="LEFT - 10" :y="y(value) + 4" text-anchor="end" font-size="9.5" fill="#9ca3af">
          {{ value }}%
        </text>
      </g>

      <path
        v-for="item in series"
        :key="`path-${item.key}`"
        :d="seriesPath(item.points)"
        fill="none"
        :stroke="item.color"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
      />

      <g v-for="item in series" :key="`dots-${item.key}`">
        <circle
          v-for="entry in dots(item.points)"
          :key="`${item.key}-${entry.point.date}`"
          :cx="x(entry.index)"
          :cy="y(entry.point.pass_rate as number)"
          r="2.8"
          fill="#fff"
          :stroke="item.color"
          stroke-width="1.8"
        >
          <title>
            {{ item.name }} · {{ entry.point.date }} · {{ rateLabel(entry.point.pass_rate) }} ·
            {{ entry.point.total }} 次
          </title>
        </circle>
      </g>

      <g>
        <rect
          v-for="(date, index) in dates"
          :key="`hit-${date}`"
          :x="x(index) - step / 2"
          :y="TOP"
          :width="Math.max(step, 1)"
          :height="BOTTOM - TOP"
          fill="transparent"
          @mouseenter="hovered = index"
          @mouseleave="hovered = null"
        />
      </g>

      <g v-if="tooltip">
        <rect
          :x="tooltip.x"
          :y="tooltip.y"
          :width="tooltip.width"
          :height="tooltip.height"
          rx="8"
          fill="#111827"
          opacity=".96"
        />
        <text :x="tooltip.x + 11" :y="tooltip.y + 17" font-size="10" fill="#e5e7eb">
          {{ tooltip.date }}
        </text>
        <g v-for="(row, index) in tooltip.rows" :key="row.key">
          <circle
            :cx="tooltip.x + 13"
            :cy="tooltip.y + 30 + index * tooltip.rowHeight"
            r="2.6"
            :fill="row.color"
          />
          <text
            :x="tooltip.x + 20"
            :y="tooltip.y + 34 + index * tooltip.rowHeight"
            font-size="9.5"
            fill="#e5e7eb"
          >
            {{ row.name.length > 14 ? `${row.name.slice(0, 14)}…` : row.name }}
          </text>
          <text
            :x="tooltip.x + tooltip.width - 11"
            :y="tooltip.y + 34 + index * tooltip.rowHeight"
            text-anchor="end"
            font-size="9.5"
            fill="#fff"
          >
            {{ row.rate }}
            <tspan fill="#9ca3af">· {{ row.measured }} 次</tspan>
          </text>
        </g>
      </g>

      <g font-size="9.5" fill="#94a3b8" text-anchor="middle">
        <template v-for="(date, index) in dates" :key="`label-${date}`">
          <text
            v-if="index % labelEvery === 0 || index === dates.length - 1"
            :x="x(index)"
            :y="BOTTOM + 18"
          >
            {{ dateLabel(date) }}
          </text>
        </template>
      </g>
    </svg>
  </div>
</template>

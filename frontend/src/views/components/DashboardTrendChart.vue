<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ExecutionDailyPoint } from '../../types'

const props = defineProps<{ points: ExecutionDailyPoint[] }>()

const WIDTH = 762
const HEIGHT = 220
const LEFT = 44
const RIGHT = 704
const TOP = 16
const BOTTOM = 192

const hovered = ref<number | null>(null)

const rates = computed(() =>
  props.points.map((item) => (item.pass_rate === null ? null : Number(item.pass_rate))),
)

const minRate = computed(() => {
  const measured = rates.value.filter((item): item is number => item !== null)
  if (!measured.length) return 80
  const floor = Math.floor(Math.min(...measured) / 5) * 5
  return Math.max(0, Math.min(80, floor))
})

const maxVolume = computed(() => {
  const totals = props.points.map((item) => item.total)
  return Math.max(1, ...totals)
})

const step = computed(() =>
  props.points.length > 1 ? (RIGHT - LEFT) / (props.points.length - 1) : 0,
)

function x(index: number): number {
  return LEFT + step.value * index
}

function y(rate: number): number {
  const span = 100 - minRate.value || 1
  return BOTTOM - ((rate - minRate.value) / span) * (BOTTOM - TOP)
}

function barHeight(total: number): number {
  return (total / maxVolume.value) * (BOTTOM - TOP)
}

const gridValues = computed(() => {
  const min = minRate.value
  const mid = Math.round((min + 100) / 2 / 5) * 5
  return [100, mid, min]
})

const linePoints = computed(() =>
  props.points
    .map((item, index) =>
      item.pass_rate === null ? null : `${x(index).toFixed(1)},${y(item.pass_rate).toFixed(1)}`,
    )
    .filter((item): item is string => item !== null)
    .join(' '),
)

const areaPath = computed(() => {
  const valid = props.points
    .map((item, index) => ({ item, index }))
    .filter((entry) => entry.item.pass_rate !== null)
  if (valid.length < 2) return ''
  const head = valid
    .map(
      (entry) =>
        `${entry.index === valid[0].index ? 'M' : 'L'}${x(entry.index).toFixed(1)},${y(
          entry.item.pass_rate as number,
        ).toFixed(1)}`,
    )
    .join(' ')
  const tailIndex = valid[valid.length - 1].index
  return `${head} L${x(tailIndex).toFixed(1)},${BOTTOM} L${x(valid[0].index).toFixed(1)},${BOTTOM} Z`
})

const active = computed(() => {
  if (hovered.value === null) return null
  const item = props.points[hovered.value]
  return item ? { item, x: x(hovered.value) } : null
})

const tooltipX = computed(() => {
  const point = active.value
  if (!point) return 0
  return Math.min(RIGHT - 116, Math.max(LEFT, point.x - 55))
})

const tooltipY = computed(() => {
  const point = active.value
  if (!point) return 0
  const base = point.item.pass_rate === null ? BOTTOM - 60 : y(point.item.pass_rate) - 40
  return Math.max(TOP + 2, base)
})

function dateLabel(value: string): string {
  return value.slice(5)
}

function rateLabel(value: number | null): string {
  return value === null ? '未判定' : `${value.toFixed(1)}%`
}
</script>

<template>
  <svg
    class="block w-full"
    :viewBox="`0 0 ${WIDTH} ${HEIGHT}`"
    role="img"
    aria-label="每日通过率与执行量趋势"
  >
    <defs>
      <linearGradient id="dashboard-area" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#4f46e5" stop-opacity=".16" />
        <stop offset="100%" stop-color="#4f46e5" stop-opacity="0" />
      </linearGradient>
    </defs>

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

    <text :x="RIGHT + 6" :y="TOP + 4" font-size="9.5" fill="#cbd5e1">{{ maxVolume }} 次</text>
    <text :x="RIGHT + 6" :y="(TOP + BOTTOM) / 2 + 4" font-size="9.5" fill="#cbd5e1">
      {{ Math.round(maxVolume / 2) }}
    </text>
    <text :x="RIGHT + 6" :y="BOTTOM + 4" font-size="9.5" fill="#cbd5e1">0</text>

    <g fill="#6366f1" opacity=".16">
      <rect
        v-for="(item, index) in points"
        :key="`bar-${item.date}`"
        :x="Math.min(x(index), RIGHT - 13) - 13"
        :y="BOTTOM - barHeight(item.total)"
        width="26"
        :height="barHeight(item.total)"
        rx="4"
      />
    </g>

    <path v-if="areaPath" :d="areaPath" fill="url(#dashboard-area)" />

    <polyline
      v-if="linePoints"
      :points="linePoints"
      fill="none"
      stroke="#4f46e5"
      stroke-width="2.2"
      stroke-linejoin="round"
      stroke-linecap="round"
    />

    <g fill="#fff" stroke="#4f46e5" stroke-width="2">
      <template v-for="(item, index) in points" :key="`dot-${item.date}`">
        <circle
          v-if="item.pass_rate !== null"
          :cx="x(index)"
          :cy="y(item.pass_rate)"
          r="3.4"
        />
      </template>
    </g>

    <g>
      <rect
        v-for="(item, index) in points"
        :key="`hit-${item.date}`"
        :x="x(index) - step / 2"
        :y="TOP"
        :width="Math.max(step, 1)"
        :height="BOTTOM - TOP"
        fill="transparent"
        @mouseenter="hovered = index"
        @mouseleave="hovered = null"
      >
        <title>{{ item.date }} · {{ rateLabel(item.pass_rate) }} · {{ item.total }} 次执行</title>
      </rect>
    </g>

    <g v-if="active">
      <rect :x="tooltipX" :y="tooltipY" width="112" height="26" rx="7" fill="#111827" />
      <text
        :x="tooltipX + 56"
        :y="tooltipY + 17"
        text-anchor="middle"
        font-size="10"
        fill="#fff"
      >
        {{ rateLabel(active.item.pass_rate) }} · {{ active.item.total }} 次
      </text>
    </g>

    <g font-size="9.5" fill="#94a3b8" text-anchor="middle">
      <text v-for="(item, index) in points" :key="`label-${item.date}`" :x="x(index)" :y="BOTTOM + 18">
        {{ dateLabel(item.date) }}
      </text>
    </g>
  </svg>
</template>

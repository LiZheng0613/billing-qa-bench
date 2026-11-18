<script setup lang="ts">
import { computed } from 'vue'
import DateTimePicker from './DateTimePicker.vue'
import TimePicker from './TimePicker.vue'
import type { ScheduleFormValue } from '../utils/schedule'
import {
  SCHEDULE_MODE_OPTIONS,
  WEEKDAY_OPTIONS,
  scheduleSummary,
  weekdayLabels,
} from '../utils/schedule'

const props = defineProps<{
  modelValue: ScheduleFormValue
  disabled?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: ScheduleFormValue): void
}>()

const value = computed(() => props.modelValue)

function patch(partial: Partial<ScheduleFormValue>): void {
  emit('update:modelValue', { ...props.modelValue, ...partial })
}

function selectType(type: ScheduleFormValue['type']): void {
  patch({ type })
}

function toggleWeekday(day: number): void {
  const exists = props.modelValue.weekdays.includes(day)
  patch({
    weekdays: exists
      ? props.modelValue.weekdays.filter((item) => item !== day)
      : [...props.modelValue.weekdays, day],
  })
}

const weekdayText = computed(() => weekdayLabels(props.modelValue.weekdays) || '未选择')

const rounds = computed(() => Math.max(1, props.modelValue.repeatRounds || 1))
const roundsHint = computed(() =>
  rounds.value > 1
    ? `共 ${rounds.value} 轮：上一轮跑完后立即自动开始下一轮`
    : '1 轮表示只执行一次；大于 1 时按轮次串行连续执行',
)
</script>

<template>
  <div class="space-y-3 rounded-xl border border-slate-200 bg-slate-50/70 p-3">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <p class="text-xs font-medium text-slate-700">执行方式</p>
      <span class="text-[11px] text-slate-500">按服务器本地时间</span>
    </div>

    <div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
      <button
        v-for="mode in SCHEDULE_MODE_OPTIONS"
        :key="mode.value"
        type="button"
        class="rounded-lg border px-2.5 py-2 text-left transition"
        :class="
          value.type === mode.value
            ? 'border-indigo-300 bg-indigo-50 text-indigo-800 ring-1 ring-indigo-200'
            : 'border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50'
        "
        :disabled="disabled"
        @click="selectType(mode.value)"
      >
        <span class="block text-xs font-medium">{{ mode.label }}</span>
        <span class="mt-0.5 block text-[10px] text-slate-500">{{ mode.hint }}</span>
      </button>
    </div>

    <div v-if="value.type === 'SCHEDULED'" class="grid gap-2 sm:max-w-xs">
      <label class="label">执行时间</label>
      <DateTimePicker
        :model-value="value.scheduledAt"
        :disabled="disabled"
        @update:model-value="patch({ scheduledAt: $event })"
      />
    </div>

    <div v-else-if="value.type === 'DAILY'" class="grid gap-2 sm:max-w-[180px]">
      <label class="label">每天执行时刻</label>
      <TimePicker
        :model-value="value.time"
        :disabled="disabled"
        @update:model-value="patch({ time: $event })"
      />
    </div>

    <div v-else-if="value.type === 'WEEKLY'" class="space-y-2">
      <div class="grid gap-2 sm:max-w-[180px]">
        <label class="label">执行时刻</label>
        <TimePicker
          :model-value="value.time"
          :disabled="disabled"
          @update:model-value="patch({ time: $event })"
        />
      </div>
      <div>
        <label class="label">执行星期（可多选）</label>
        <div class="mt-1 flex flex-wrap gap-1.5">
          <button
            v-for="day in WEEKDAY_OPTIONS"
            :key="day.value"
            type="button"
            class="rounded-full border px-2.5 py-1 text-[11px] transition"
            :class="
              value.weekdays.includes(day.value)
                ? 'border-indigo-300 bg-indigo-50 text-indigo-700'
                : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
            "
            :disabled="disabled"
            @click="toggleWeekday(day.value)"
          >
            {{ day.label }}
          </button>
        </div>
        <p class="mt-1 text-[10px] text-slate-500">已选：{{ weekdayText }}</p>
      </div>
    </div>

    <div class="grid gap-2 sm:max-w-xs">
      <label class="label">连续轮次</label>
      <div class="flex items-center gap-2">
        <input
          :value="rounds"
          class="input w-24 text-center tabular-nums"
          type="number"
          min="1"
          max="20"
          :disabled="disabled"
          @input="patch({ repeatRounds: Number(($event.target as HTMLInputElement).value) || 1 })"
        />
        <span class="text-[11px] text-slate-500">轮（含第一轮）</span>
      </div>
      <p class="text-[10px] text-slate-500">{{ roundsHint }}</p>
    </div>

    <div
      class="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-[11px] text-indigo-700"
    >
      {{ scheduleSummary(value) }}
      <span v-if="value.type !== 'IMMEDIATE'" class="text-indigo-500">
        · 每次触发都会生成一份新的批量任务，历史记录独立保留
      </span>
    </div>
  </div>
</template>

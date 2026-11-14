<script setup lang="ts">
import { computed } from 'vue'
import { statusLabel } from '../utils/format'

const props = withDefaults(defineProps<{ status?: string | null; size?: 'sm' | 'md' }>(), {
  status: null,
  size: 'md',
})

const tone = computed(() => {
  const value = (props.status ?? '').toUpperCase()
  if (['PASS', 'COMPLETED', 'ACTIVE', 'UP', 'GOAL_ACHIEVED', 'SUCCESS'].includes(value)) {
    return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  }
  if (['FAIL', 'ERROR', 'FAILED', 'DOWN', 'REPETITION_ABORT'].includes(value)) {
    return 'border-rose-200 bg-rose-50 text-rose-700'
  }
  if (['MAX_TURNS_EXCEEDED', 'PAUSED', 'GIVE_UP'].includes(value)) {
    return 'border-amber-200 bg-amber-50 text-amber-700'
  }
  if (['RUNNING', 'SANDBOX'].includes(value)) {
    return 'border-indigo-200 bg-indigo-50 text-indigo-700'
  }
  if (['PENDING', 'DRAFT', 'ABORTED', 'DISABLED'].includes(value)) {
    return 'border-slate-200 bg-slate-100 text-slate-600'
  }
  return 'border-sky-200 bg-sky-50 text-sky-700'
})

const dotTone = computed(() => {
  const value = (props.status ?? '').toUpperCase()
  if (['PASS', 'COMPLETED', 'ACTIVE', 'UP', 'GOAL_ACHIEVED'].includes(value)) return 'bg-emerald-500'
  if (['FAIL', 'ERROR', 'FAILED', 'DOWN', 'REPETITION_ABORT'].includes(value)) return 'bg-rose-500'
  if (['MAX_TURNS_EXCEEDED', 'PAUSED', 'GIVE_UP'].includes(value)) return 'bg-amber-500'
  if (['RUNNING', 'SANDBOX'].includes(value)) return 'bg-indigo-500 animate-pulse'
  return 'bg-slate-400'
})
</script>

<template>
  <span
    class="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border font-medium"
    :class="[tone, size === 'sm' ? 'px-2 py-0.5 text-[10px]' : 'px-2.5 py-0.5 text-[11px]']"
  >
    <span class="h-1.5 w-1.5 rounded-full" :class="dotTone" aria-hidden="true" />
    {{ statusLabel(status) }}
  </span>
</template>

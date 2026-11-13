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
    return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
  }
  if (['FAIL', 'ERROR', 'FAILED', 'DOWN', 'REPETITION_ABORT'].includes(value)) {
    return 'border-rose-500/30 bg-rose-500/10 text-rose-300'
  }
  if (['MAX_TURNS_EXCEEDED', 'PAUSED', 'GIVE_UP'].includes(value)) {
    return 'border-amber-500/30 bg-amber-500/10 text-amber-300'
  }
  if (['RUNNING', 'SANDBOX'].includes(value)) {
    return 'border-indigo-500/30 bg-indigo-500/10 text-indigo-300'
  }
  if (['PENDING', 'DRAFT', 'ABORTED', 'DISABLED'].includes(value)) {
    return 'border-slate-600/40 bg-slate-700/20 text-slate-300'
  }
  return 'border-sky-500/30 bg-sky-500/10 text-sky-300'
})

const dotTone = computed(() => {
  const value = (props.status ?? '').toUpperCase()
  if (['PASS', 'COMPLETED', 'ACTIVE', 'UP', 'GOAL_ACHIEVED'].includes(value)) return 'bg-emerald-400'
  if (['FAIL', 'ERROR', 'FAILED', 'DOWN', 'REPETITION_ABORT'].includes(value)) return 'bg-rose-400'
  if (['MAX_TURNS_EXCEEDED', 'PAUSED', 'GIVE_UP'].includes(value)) return 'bg-amber-400'
  if (['RUNNING', 'SANDBOX'].includes(value)) return 'bg-indigo-400 animate-pulse'
  return 'bg-slate-400'
})
</script>

<template>
  <span
    class="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border font-medium"
    :class="[tone, size === 'sm' ? 'px-1.5 py-0.5 text-[10px]' : 'px-2 py-0.5 text-[11px]']"
  >
    <span class="h-1.5 w-1.5 rounded-full" :class="dotTone" />
    {{ statusLabel(status) }}
  </span>
</template>

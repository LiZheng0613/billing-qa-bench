<script setup lang="ts">
withDefaults(
  defineProps<{
    label: string
    value: string | number
    hint?: string
    accent?: 'indigo' | 'emerald' | 'amber' | 'rose' | 'sky'
  }>(),
  { hint: '', accent: 'indigo' },
)

const accentMap = {
  indigo: 'text-indigo-300',
  emerald: 'text-emerald-300',
  amber: 'text-amber-300',
  rose: 'text-rose-300',
  sky: 'text-sky-300',
} as const
</script>

<template>
  <div class="card px-4 py-3">
    <p class="text-[11px] uppercase tracking-wider text-slate-500">{{ label }}</p>
    <p class="mt-1 text-2xl font-semibold tabular-nums" :class="accentMap[accent]">{{ value }}</p>
    <p v-if="hint" class="mt-0.5 text-[11px] text-slate-500">{{ hint }}</p>
  </div>
</template>

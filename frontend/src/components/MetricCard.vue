<script setup lang="ts">
withDefaults(
  defineProps<{
    label: string
    value: string | number
    hint?: string
    accent?: 'indigo' | 'emerald' | 'amber' | 'rose' | 'sky'
    progress?: number | null
  }>(),
  { hint: '', accent: 'indigo', progress: null },
)

const accentMap = {
  indigo: { text: 'text-indigo-600', dot: 'bg-indigo-500', bar: 'bg-indigo-500' },
  emerald: { text: 'text-emerald-600', dot: 'bg-emerald-500', bar: 'bg-emerald-500' },
  amber: { text: 'text-amber-600', dot: 'bg-amber-500', bar: 'bg-amber-500' },
  rose: { text: 'text-rose-600', dot: 'bg-rose-500', bar: 'bg-rose-500' },
  sky: { text: 'text-sky-600', dot: 'bg-sky-500', bar: 'bg-sky-500' },
} as const
</script>

<template>
  <div class="card flex flex-col px-5 py-4">
    <p class="flex items-center gap-2 text-[10.5px] font-medium uppercase tracking-[0.08em] text-slate-500">
      <span class="h-1.5 w-1.5 rounded-full" :class="accentMap[accent].dot" aria-hidden="true" />
      {{ label }}
    </p>
    <p class="mt-2 text-[28px] font-semibold leading-none tracking-[-0.03em] tabular-nums" :class="accentMap[accent].text">
      {{ value }}
    </p>
    <div
      v-if="progress !== null"
      class="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-slate-100"
      role="progressbar"
      :aria-valuenow="Math.round(progress)"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-label="label"
    >
      <div
        class="h-full rounded-full transition-all duration-500"
        :class="accentMap[accent].bar"
        :style="{ width: `${Math.min(100, Math.max(0, progress ?? 0))}%` }"
      />
    </div>
    <p v-if="hint" class="mt-2 truncate text-[11px] text-slate-500">{{ hint }}</p>
  </div>
</template>

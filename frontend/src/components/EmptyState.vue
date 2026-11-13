<script setup lang="ts">
withDefaults(defineProps<{ title?: string; description?: string; compact?: boolean }>(), {
  title: '暂无数据',
  description: '',
  compact: false,
})
</script>

<template>
  <div
    class="flex flex-col items-center justify-center gap-2 text-center"
    :class="compact ? 'py-6' : 'py-14'"
  >
    <div class="flex h-11 w-11 items-center justify-center rounded-full border border-slate-800 bg-slate-900">
      <svg class="h-5 w-5 text-slate-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
        <path stroke-linecap="round" stroke-linejoin="round" d="M4 7h16M4 12h10M4 17h7" />
      </svg>
    </div>
    <p class="text-sm font-medium text-slate-300">{{ title }}</p>
    <p v-if="description" class="max-w-sm text-xs leading-relaxed text-slate-500">{{ description }}</p>
    <slot />
  </div>
</template>

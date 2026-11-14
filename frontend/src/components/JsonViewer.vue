<script setup lang="ts">
import { computed } from 'vue'
import { useToastStore } from '../stores/toast'

const props = withDefaults(
  defineProps<{
    value: unknown
    maxHeight?: string
    label?: string
  }>(),
  { maxHeight: '320px', label: '' },
)

const toast = useToastStore()

const text = computed(() => {
  if (props.value === undefined || props.value === null) return '无内容'
  if (typeof props.value === 'string') return props.value
  try {
    return JSON.stringify(props.value, null, 2)
  } catch {
    return String(props.value)
  }
})

async function copy(): Promise<void> {
  try {
    await navigator.clipboard.writeText(text.value)
    toast.success('已复制到剪贴板')
  } catch {
    toast.error('复制失败，请手动选择文本')
  }
}
</script>

<template>
  <div class="overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
    <div class="flex items-center justify-between border-b border-slate-200 px-3 py-1.5">
      <span class="text-[11px] uppercase tracking-wider text-slate-500">{{ label || 'JSON' }}</span>
      <button class="text-[11px] text-slate-500 transition hover:text-slate-800" @click="copy">复制</button>
    </div>
    <pre
      class="mono overflow-auto p-3 text-slate-700"
      :style="{ maxHeight }"
    >{{ text }}</pre>
  </div>
</template>

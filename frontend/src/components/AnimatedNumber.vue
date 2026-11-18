<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'

/**
 * 数字滚动：挂载 / 值变化时从当前值缓动到目标值（调试完成分数滚动用）。
 * 开启「减少动效」偏好时直接显示目标值。
 */
const props = defineProps<{ value: number }>()

const shown = ref(0)
let raf: number | null = null
let settle: number | null = null

function animate(target: number): void {
  if (raf !== null) cancelAnimationFrame(raf)
  if (settle !== null) clearTimeout(settle)
  const from = shown.value
  const started = performance.now()
  const duration = 450
  const step = (now: number): void => {
    const progress = Math.min(1, (now - started) / duration)
    const eased = 1 - Math.pow(1 - progress, 3)
    shown.value = Math.round(from + (target - from) * eased)
    if (progress < 1) raf = requestAnimationFrame(step)
  }
  raf = requestAnimationFrame(step)
  // 后台标签页 rAF 会被挂起：到点兜底直接落到终值，避免长期显示 0
  settle = window.setTimeout(() => {
    shown.value = target
    if (raf !== null) {
      cancelAnimationFrame(raf)
      raf = null
    }
  }, duration + 80)
}

watch(() => props.value, (value) => animate(value), { immediate: true })

onBeforeUnmount(() => {
  if (raf !== null) cancelAnimationFrame(raf)
  if (settle !== null) clearTimeout(settle)
})
</script>

<template>{{ shown }}</template>

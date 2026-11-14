<script setup lang="ts">
import { useToastStore } from '../stores/toast'

const toast = useToastStore()

const toneMap = {
  success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  error: 'border-rose-200 bg-rose-50 text-rose-700',
  info: 'border-sky-200 bg-sky-50 text-sky-700',
} as const

const iconMap = {
  success: 'M5 13l4 4L19 7',
  error: 'M12 8v5m0 3v.5M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z',
  info: 'M12 8h.01M11 12h1v5h1M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z',
} as const
</script>

<template>
  <div class="pointer-events-none fixed inset-x-0 top-4 z-[100] flex flex-col items-center gap-2 px-4">
    <TransitionGroup name="toast">
      <div
        v-for="item in toast.items"
        :key="item.id"
        class="pointer-events-auto flex w-full max-w-md items-start gap-3 rounded-2xl border px-4 py-3 shadow-tile-lift"
        :class="toneMap[item.type]"
      >
        <svg class="mt-0.5 h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" :d="iconMap[item.type]" />
        </svg>
        <p class="flex-1 whitespace-pre-wrap text-sm leading-relaxed">{{ item.message }}</p>
        <button class="shrink-0 opacity-60 transition hover:opacity-100" @click="toast.remove(item.id)">
          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-enter-active,
.toast-leave-active {
  transition: all 0.22s ease;
}
.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateY(-8px) scale(0.98);
}
.toast-move {
  transition: transform 0.22s ease;
}
</style>

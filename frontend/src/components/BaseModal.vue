<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { isConfirmOpen } from '../composables/useConfirm'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    title?: string
    subtitle?: string
    width?: 'sm' | 'md' | 'lg' | 'xl' | 'full'
    closeOnBackdrop?: boolean
  }>(),
  { title: '', subtitle: '', width: 'lg', closeOnBackdrop: true },
)

const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()

const widthClass = computed(() => {
  switch (props.width) {
    case 'sm':
      return 'max-w-md'
    case 'md':
      return 'max-w-2xl'
    case 'xl':
      return 'max-w-6xl'
    case 'full':
      return 'max-w-[96vw]'
    default:
      return 'max-w-4xl'
  }
})

const overlayRef = ref<HTMLElement | null>(null)

function close(): void {
  emit('update:modelValue', false)
}

function onKeydown(event: KeyboardEvent): void {
  // 确认弹窗叠在上层时，Esc 由 ConfirmDialog 处理，避免同时关闭两层弹窗
  if (isConfirmOpen()) return
  if (event.key === 'Escape' && props.modelValue) close()
}

watch(
  () => props.modelValue,
  (open) => {
    document.body.style.overflow = open ? 'hidden' : ''
  },
)

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = ''
  // 路由切换时组件可能在弹窗关闭动画未结束时被卸载，Teleport 到 body 的节点存在残留风险。
  // 这里主动摘除遮罩，避免新页面出现无法关闭的幽灵弹窗。
  if (overlayRef.value) {
    overlayRef.value.remove()
    overlayRef.value = null
  }
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="modelValue"
      ref="overlayRef"
      class="modal-overlay fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/40 p-3 backdrop-blur-sm sm:p-6"
      @click.self="closeOnBackdrop && close()"
    >
      <div class="card my-4 w-full animate-fade-up sm:my-8" :class="widthClass">
        <header class="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4">
          <div class="min-w-0">
            <h2 class="truncate text-base font-semibold text-slate-900">{{ title }}</h2>
            <p v-if="subtitle" class="mt-1 text-xs text-slate-500">{{ subtitle }}</p>
          </div>
          <button
            class="rounded-lg p-1.5 text-slate-500 transition hover:bg-slate-100 hover:text-slate-800"
            aria-label="关闭"
            @click="close"
          >
            <svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </header>
        <div class="max-h-[72vh] overflow-y-auto px-5 py-4">
          <slot />
        </div>
        <footer
          v-if="$slots.footer"
          class="flex flex-wrap items-center justify-end gap-2 border-t border-slate-200 px-5 py-3"
        >
          <slot name="footer" />
        </footer>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* 不使用 <Transition>：路由切换时 leave 过渡中的 Teleport 节点可能残留为幽灵弹窗。
   进入动画由关键帧完成，关闭时同步移除。 */
.modal-overlay {
  animation: modal-fade-in 0.18s ease-out;
}
@keyframes modal-fade-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

/* 减少动效偏好：遮罩与卡片改为纯淡入（去掉上移动效），仍保留短暂的"出现"过渡 */
@media (prefers-reduced-motion: reduce) {
  .modal-overlay {
    animation: modal-fade-in 0.14s ease-out !important;
  }
  .modal-overlay .card {
    animation: modal-fade-in 0.16s ease-out !important;
  }
}
</style>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from 'vue'

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

function close(): void {
  emit('update:modelValue', false)
}

function onKeydown(event: KeyboardEvent): void {
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
})
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div
        v-if="modelValue"
        class="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-950/80 p-3 backdrop-blur-sm sm:p-6"
        @click.self="closeOnBackdrop && close()"
      >
        <div class="card my-4 w-full animate-fade-up sm:my-8" :class="widthClass">
          <header class="flex items-start justify-between gap-4 border-b border-slate-800 px-5 py-4">
            <div class="min-w-0">
              <h2 class="truncate text-base font-semibold text-slate-100">{{ title }}</h2>
              <p v-if="subtitle" class="mt-1 text-xs text-slate-500">{{ subtitle }}</p>
            </div>
            <button
              class="rounded-lg p-1.5 text-slate-500 transition hover:bg-slate-800 hover:text-slate-200"
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
            class="flex flex-wrap items-center justify-end gap-2 border-t border-slate-800 px-5 py-3"
          >
            <slot name="footer" />
          </footer>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.2s ease;
}
.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}
.modal-enter-active .card {
  transition: transform 0.2s ease;
}
.modal-enter-from .card {
  transform: translateY(10px);
}
</style>

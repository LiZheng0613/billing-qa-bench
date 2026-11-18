<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useEscLayer } from '../composables/useEscLayer'

/**
 * 自定义时刻选择器（HH:mm）。
 *
 * 原生 <input type="time"> 的弹层由浏览器绘制，无法跟随项目设计语言；
 * 这里用与 .input / 下拉面板一致的样式实现小时 + 分钟双列选择。
 */
const props = defineProps<{
  modelValue: string
  disabled?: boolean
}>()

const emit = defineEmits<{ (e: 'update:modelValue', value: string): void }>()

const HOURS = Array.from({ length: 24 }, (_, index) => String(index).padStart(2, '0'))
const MINUTES = Array.from({ length: 60 }, (_, index) => String(index).padStart(2, '0'))

const open = ref(false)
const dropUp = ref(false)
const rootRef = ref<HTMLElement | null>(null)
const triggerRef = ref<HTMLElement | null>(null)
const hoursRef = ref<HTMLElement | null>(null)
const minutesRef = ref<HTMLElement | null>(null)

useEscLayer(open)

const current = computed(() => {
  const matched = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(props.modelValue || '')
  return matched ? { hour: matched[1], minute: matched[2] } : { hour: '09', minute: '00' }
})

function patch(hour: string, minute: string): void {
  emit('update:modelValue', `${hour}:${minute}`)
}

function place(): void {
  const rect = triggerRef.value?.getBoundingClientRect()
  if (!rect) return
  // 面板高度约 300px：下方空间不足时向上展开，避免被弹窗滚动容器裁切
  dropUp.value = window.innerHeight - rect.bottom < 320 && rect.top > 320
}

function scrollActiveIntoView(container: HTMLElement | null): void {
  if (!container) return
  const active = container.querySelector<HTMLElement>('[data-active="true"]')
  if (!active) return
  container.scrollTop = Math.max(
    0,
    active.offsetTop - container.clientHeight / 2 + active.clientHeight / 2,
  )
}

async function revealSelection(): Promise<void> {
  await nextTick()
  scrollActiveIntoView(hoursRef.value)
  scrollActiveIntoView(minutesRef.value)
}

async function toggle(): Promise<void> {
  if (props.disabled) return
  if (open.value) {
    open.value = false
    return
  }
  place()
  open.value = true
  await revealSelection()
}

function close(): void {
  open.value = false
}

function setNow(): void {
  const date = new Date()
  patch(String(date.getHours()).padStart(2, '0'), String(date.getMinutes()).padStart(2, '0'))
  void revealSelection()
}

function onDocumentPointer(event: PointerEvent): void {
  if (!open.value) return
  if (rootRef.value && !rootRef.value.contains(event.target as Node)) close()
}

function onKeydown(event: KeyboardEvent): void {
  // 弹层打开时 Esc 先关闭弹层，并不再传递给外层弹窗。
  // 无焦点元素时按键事件直接以 window 为目标，此时捕获/冒泡监听器都在目标阶段执行，
  // 必须用 stopImmediatePropagation 才能拦住外层弹窗的 Esc。
  if (open.value && event.key === 'Escape') {
    event.stopImmediatePropagation()
    close()
  }
}

watch(open, (value) => {
  if (value) void revealSelection()
})

onMounted(() => {
  document.addEventListener('pointerdown', onDocumentPointer)
  window.addEventListener('keydown', onKeydown, true)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointer)
  window.removeEventListener('keydown', onKeydown, true)
})
</script>

<template>
  <div ref="rootRef" class="relative">
    <button
      ref="triggerRef"
      type="button"
      class="input flex items-center justify-between gap-2 text-left tabular-nums"
      :class="{ 'border-brand ring-2 ring-brand/15': open }"
      :disabled="disabled"
      :aria-expanded="open"
      aria-haspopup="listbox"
      @click="toggle"
    >
      <span>{{ current.hour }}:{{ current.minute }}</span>
      <svg
        class="h-4 w-4 shrink-0 text-slate-400"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        aria-hidden="true"
      >
        <circle cx="12" cy="12" r="9" />
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 7v5l3 2" />
      </svg>
    </button>

    <Transition
      enter-active-class="transition duration-150 ease-out"
      enter-from-class="opacity-0 -translate-y-1"
      enter-to-class="opacity-100 translate-y-0"
    >
      <div
        v-if="open"
        class="absolute left-0 z-30 w-56 rounded-xl border border-line bg-white p-2 shadow-[0_1px_2px_rgba(17,24,39,0.04),0_18px_40px_-20px_rgba(17,24,39,0.28)]"
        :class="dropUp ? 'bottom-full mb-1.5' : 'top-full mt-1.5'"
        role="listbox"
        aria-label="选择时间"
      >
        <div class="grid grid-cols-2 gap-2">
          <div>
            <p class="mb-1 px-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">
              时
            </p>
            <div ref="hoursRef" class="relative max-h-44 overflow-y-auto overscroll-contain pr-0.5">
              <button
                v-for="item in HOURS"
                :key="item"
                type="button"
                class="menu-option"
                :class="
                  item === current.hour
                    ? 'bg-brand-soft font-medium text-brand'
                    : 'text-slate-600 hover:bg-slate-50'
                "
                :data-active="item === current.hour"
                :aria-selected="item === current.hour"
                role="option"
                @click="patch(item, current.minute)"
              >
                {{ item }}
              </button>
            </div>
          </div>
          <div>
            <p class="mb-1 px-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">
              分
            </p>
            <div ref="minutesRef" class="relative max-h-44 overflow-y-auto overscroll-contain pr-0.5">
              <button
                v-for="item in MINUTES"
                :key="item"
                type="button"
                class="menu-option"
                :class="
                  item === current.minute
                    ? 'bg-brand-soft font-medium text-brand'
                    : 'text-slate-600 hover:bg-slate-50'
                "
                :data-active="item === current.minute"
                :aria-selected="item === current.minute"
                role="option"
                @click="patch(current.hour, item)"
              >
                {{ item }}
              </button>
            </div>
          </div>
        </div>

        <div class="mt-2 flex items-center justify-between border-t border-line-soft pt-2">
          <button
            type="button"
            class="rounded-lg px-2 py-1 text-[11px] text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
            @click="setNow"
          >
            此刻
          </button>
          <button
            type="button"
            class="rounded-lg bg-brand px-2.5 py-1 text-[11px] font-medium text-white transition hover:bg-brand-deep"
            @click="close"
          >
            完成
          </button>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.menu-option {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: center;
  border-radius: 0.5rem;
  padding: 0.375rem 0.5rem;
  font-size: 0.75rem;
  line-height: 1rem;
  font-variant-numeric: tabular-nums;
  transition: background-color 0.12s ease, color 0.12s ease;
}
</style>

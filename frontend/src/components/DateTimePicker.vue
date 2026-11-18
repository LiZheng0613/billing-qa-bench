<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useEscLayer } from '../composables/useEscLayer'

/**
 * 自定义日期时间选择器（YYYY-MM-DDTHH:mm）。
 *
 * 原生 <input type="datetime-local"> 的日历弹层由浏览器绘制，无法跟随项目设计；
 * 这里用与 .input / .card 一致的样式实现「月历 + 时刻」面板。
 */
const props = defineProps<{
  modelValue: string
  disabled?: boolean
}>()

const emit = defineEmits<{ (e: 'update:modelValue', value: string): void }>()

const WEEKDAY_LABELS = ['一', '二', '三', '四', '五', '六', '日']
const HOURS = Array.from({ length: 24 }, (_, index) => String(index).padStart(2, '0'))
const MINUTES = Array.from({ length: 60 }, (_, index) => String(index).padStart(2, '0'))

const open = ref(false)
const dropUp = ref(false)
const rootRef = ref<HTMLElement | null>(null)
const triggerRef = ref<HTMLElement | null>(null)
const viewYear = ref(new Date().getFullYear())
const viewMonth = ref(new Date().getMonth())

useEscLayer(open)

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

const current = computed<Date | null>(() => {
  if (!props.modelValue) return null
  const date = new Date(props.modelValue)
  return Number.isNaN(date.getTime()) ? null : date
})

const label = computed(() => {
  const date = current.value
  if (!date) return '选择时间'
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
})

/** 面板内以选中值（无值时以「一小时后」）作为初始视图与默认时刻。 */
const draft = computed<Date>(() => current.value ?? new Date(Date.now() + 60 * 60 * 1000))

const selectedKey = computed(() => {
  const date = current.value
  return date ? `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}` : ''
})

const todayKey = (() => {
  const date = new Date()
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`
})()

const days = computed(() => {
  const first = new Date(viewYear.value, viewMonth.value, 1)
  const offset = (first.getDay() + 6) % 7
  const startOfToday = new Date()
  startOfToday.setHours(0, 0, 0, 0)
  const cells: Array<{
    key: string
    day: number
    year: number
    month: number
    inMonth: boolean
    past: boolean
    time: number
  }> = []
  for (let index = 0; index < 42; index += 1) {
    const date = new Date(viewYear.value, viewMonth.value, 1 - offset + index)
    cells.push({
      key: `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`,
      day: date.getDate(),
      year: date.getFullYear(),
      month: date.getMonth(),
      inMonth: date.getMonth() === viewMonth.value,
      past: date.getTime() < startOfToday.getTime(),
      time: date.getTime(),
    })
  }
  return cells
})

function emitValue(date: Date): void {
  emit(
    'update:modelValue',
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`,
  )
}

function selectDay(year: number, month: number, day: number): void {
  const base = draft.value
  const next = new Date(year, month, day, base.getHours(), base.getMinutes())
  emitValue(next)
  viewYear.value = year
  viewMonth.value = month
}

function selectHour(value: string): void {
  const base = draft.value
  emitValue(
    new Date(base.getFullYear(), base.getMonth(), base.getDate(), Number(value), base.getMinutes()),
  )
}

function selectMinute(value: string): void {
  const base = draft.value
  emitValue(
    new Date(base.getFullYear(), base.getMonth(), base.getDate(), base.getHours(), Number(value)),
  )
}

function shiftMonth(step: number): void {
  const next = new Date(viewYear.value, viewMonth.value + step, 1)
  viewYear.value = next.getFullYear()
  viewMonth.value = next.getMonth()
}

function syncView(): void {
  const date = draft.value
  viewYear.value = date.getFullYear()
  viewMonth.value = date.getMonth()
}

function place(): void {
  const rect = triggerRef.value?.getBoundingClientRect()
  if (!rect) return
  // 面板高度约 380px：下方空间不足时向上展开，避免被弹窗滚动容器裁切
  dropUp.value = window.innerHeight - rect.bottom < 400 && rect.top > 400
}

function toggle(): void {
  if (props.disabled) return
  if (open.value) {
    open.value = false
    return
  }
  syncView()
  place()
  open.value = true
}

function close(): void {
  open.value = false
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
  if (value) syncView()
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
      aria-haspopup="dialog"
      @click="toggle"
    >
      <span :class="{ 'text-slate-400': !current }">{{ label }}</span>
      <svg
        class="h-4 w-4 shrink-0 text-slate-400"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        aria-hidden="true"
      >
        <rect x="3.5" y="5" width="17" height="15.5" rx="2.5" />
        <path stroke-linecap="round" d="M8 3.5v3M16 3.5v3M3.5 10h17" />
      </svg>
    </button>

    <Transition
      enter-active-class="transition duration-150 ease-out"
      enter-from-class="opacity-0 -translate-y-1"
      enter-to-class="opacity-100 translate-y-0"
    >
      <div
        v-if="open"
        class="absolute left-0 z-30 w-[272px] rounded-xl border border-line bg-white p-3 shadow-[0_1px_2px_rgba(17,24,39,0.04),0_18px_40px_-20px_rgba(17,24,39,0.28)]"
        :class="dropUp ? 'bottom-full mb-1.5' : 'top-full mt-1.5'"
        role="dialog"
        aria-label="选择执行时间"
      >
        <div class="mb-2 flex items-center justify-between">
          <button
            type="button"
            class="flex h-7 w-7 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-100 hover:text-slate-800"
            aria-label="上个月"
            @click="shiftMonth(-1)"
          >
            <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="m15 6-6 6 6 6" />
            </svg>
          </button>
          <p class="text-xs font-semibold text-slate-800">
            {{ viewYear }} 年 {{ viewMonth + 1 }} 月
          </p>
          <button
            type="button"
            class="flex h-7 w-7 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-100 hover:text-slate-800"
            aria-label="下个月"
            @click="shiftMonth(1)"
          >
            <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="m9 6 6 6-6 6" />
            </svg>
          </button>
        </div>

        <div class="grid grid-cols-7 gap-1">
          <span
            v-for="weekday in WEEKDAY_LABELS"
            :key="weekday"
            class="flex h-6 items-center justify-center text-[10px] font-medium text-slate-400"
          >
            {{ weekday }}
          </span>
          <button
            v-for="cell in days"
            :key="cell.key"
            type="button"
            class="flex h-8 items-center justify-center rounded-lg text-xs tabular-nums transition"
            :class="[
              cell.key === selectedKey
                ? 'bg-brand font-medium text-white'
                : cell.past
                  ? 'cursor-not-allowed text-slate-300'
                  : cell.inMonth
                    ? 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                    : 'text-slate-300 hover:bg-slate-100 hover:text-slate-600',
              cell.key === todayKey && cell.key !== selectedKey ? 'ring-1 ring-brand/40' : '',
            ]"
            :disabled="cell.past"
            @click="selectDay(cell.year, cell.month, cell.day)"
          >
            {{ cell.day }}
          </button>
        </div>

        <div class="mt-2.5 flex items-center gap-1.5 border-t border-line-soft pt-2.5">
          <span class="text-[11px] text-slate-500">时间</span>
          <select
            class="input w-[62px] px-2 py-1 text-xs tabular-nums"
            :value="pad(draft.getHours())"
            @change="selectHour(($event.target as HTMLSelectElement).value)"
          >
            <option v-for="item in HOURS" :key="item" :value="item">{{ item }}</option>
          </select>
          <span class="text-xs text-slate-400">:</span>
          <select
            class="input w-[62px] px-2 py-1 text-xs tabular-nums"
            :value="pad(draft.getMinutes())"
            @change="selectMinute(($event.target as HTMLSelectElement).value)"
          >
            <option v-for="item in MINUTES" :key="item" :value="item">{{ item }}</option>
          </select>
          <button
            type="button"
            class="ml-auto rounded-lg bg-brand px-2.5 py-1 text-[11px] font-medium text-white transition hover:bg-brand-deep"
            @click="close"
          >
            完成
          </button>
        </div>
      </div>
    </Transition>
  </div>
</template>

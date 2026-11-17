<script setup lang="ts">
/**
 * 统一确认弹窗。
 * 由 App.vue 挂载一次，业务侧通过 useConfirm()/confirmAction() 调用。
 *
 * 交互与视觉与 BaseModal 保持一致：
 * - 遮罩 bg-slate-900/40 + backdrop-blur，卡片复用 .card / animate-fade-up
 * - Esc / 点击遮罩 = 取消，焦点默认落在「取消」上，避免误触删除
 * - 打开时锁定页面滚动，关闭后把焦点还给触发元素
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { confirmState, settleConfirm, type ConfirmIcon, type ConfirmTone } from '../composables/useConfirm'

const state = confirmState()
const cardRef = ref<HTMLElement | null>(null)
const cancelRef = ref<HTMLButtonElement | null>(null)
let restoreFocus: HTMLElement | null = null
// 记录打开前的 overflow：确认弹窗可能叠在 BaseModal 之上，关闭后需还原为 'hidden' 而不是 ''
let prevOverflow = ''

const TONE_ICON_CLASS: Record<ConfirmTone, string> = {
  danger: 'bg-rose-50 text-rose-600 ring-1 ring-rose-100',
  warning: 'bg-amber-50 text-amber-600 ring-1 ring-amber-100',
  primary: 'bg-brand-soft text-brand ring-1 ring-brand-edge',
}

const ctaClass = computed(() =>
  state.tone === 'danger'
    ? 'border-rose-600 bg-rose-600 text-white hover:border-rose-700 hover:bg-rose-700'
    : 'border-brand bg-brand text-white shadow-[0_10px_24px_-16px_rgba(79,70,229,0.9)] hover:border-brand-deep hover:bg-brand-deep',
)

const ICON_PATH: Record<ConfirmIcon, string> = {
  trash: 'M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m-9 0 1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13',
  warning: 'M12 9v4m0 4h.01M10.3 4.2 2.7 17.5A1.8 1.8 0 0 0 4.3 20h15.4a1.8 1.8 0 0 0 1.6-2.5L13.7 4.2a1.8 1.8 0 0 0-3.4 0Z',
}

/** 供 aria-describedby 使用：只包含实际渲染出来的说明节点 id。 */
const describedBy = computed(() => {
  const ids: string[] = []
  if (state.message) ids.push('confirm-desc')
  if (state.detail) ids.push('confirm-detail')
  return ids.length ? ids.join(' ') : undefined
})

function cancel(): void {
  settleConfirm(false)
}

function accept(): void {
  settleConfirm(true)
}

function onKeydown(event: KeyboardEvent): void {
  if (!state.open) return
  // 捕获阶段拦截：无论下层 Modal 何时注册监听，都不会同时响应本次按键
  event.stopPropagation()
  if (event.key === 'Escape') {
    event.preventDefault()
    cancel()
    return
  }
  // 简易焦点圈：Tab 在「取消」「确认」之间循环，避免焦点跑到弹窗外
  if (event.key === 'Tab' && cardRef.value) {
    const focusables = Array.from(
      cardRef.value.querySelectorAll<HTMLElement>('button:not([disabled])'),
    )
    if (focusables.length === 0) return
    const first = focusables[0]
    const last = focusables[focusables.length - 1]
    const active = document.activeElement as HTMLElement | null
    if (event.shiftKey && active === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && active === last) {
      event.preventDefault()
      first.focus()
    }
  }
}

watch(
  () => state.open,
  (open) => {
    if (open) {
      prevOverflow = document.body.style.overflow
      document.body.style.overflow = 'hidden'
      restoreFocus = document.activeElement as HTMLElement | null
      void nextTick(() => cancelRef.value?.focus())
    } else {
      // 还原打开前的滚动锁状态：下层若仍有 BaseModal，应保持 hidden
      document.body.style.overflow = prevOverflow
      restoreFocus?.focus?.()
      restoreFocus = null
    }
  },
)

onMounted(() => window.addEventListener('keydown', onKeydown, true))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown, true)
  if (state.open) document.body.style.overflow = prevOverflow
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="state.open"
      :key="state.seq"
      class="confirm-overlay fixed inset-0 z-[60] flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-sm"
      @click.self="cancel"
    >
      <div
        ref="cardRef"
        class="confirm-card card w-full max-w-md animate-fade-up overflow-hidden"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        :aria-describedby="describedBy"
      >
        <div class="px-5 pt-5">
          <div class="flex items-start gap-3">
            <span
              class="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl"
              :class="TONE_ICON_CLASS[state.tone]"
              aria-hidden="true"
            >
              <svg
                class="h-5 w-5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <path :d="ICON_PATH[state.icon]" />
              </svg>
            </span>
            <div class="min-w-0 flex-1">
              <h2 id="confirm-title" class="text-base font-semibold leading-6 text-ink">
                {{ state.title }}
              </h2>
              <p
                v-if="state.message"
                id="confirm-desc"
                class="mt-1.5 whitespace-pre-line text-sm leading-relaxed text-ink-soft"
              >
                {{ state.message }}
              </p>
            </div>
          </div>
          <p
            v-if="state.detail"
            id="confirm-detail"
            class="mt-3 rounded-xl border border-line-soft bg-canvas px-3 py-2 text-xs leading-relaxed text-slate-500"
          >
            {{ state.detail }}
          </p>
        </div>
        <footer
          class="mt-5 flex flex-wrap items-center justify-end gap-2 border-t border-line-soft bg-canvas/60 px-5 py-3"
        >
          <button ref="cancelRef" type="button" class="btn" @click="cancel">
            {{ state.cancelText }}
          </button>
          <button
            type="button"
            class="inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-xl border px-3 py-2 text-sm font-medium transition duration-150 disabled:cursor-not-allowed disabled:opacity-45"
            :class="ctaClass"
            @click="accept"
          >
            {{ state.confirmText }}
          </button>
        </footer>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.confirm-overlay {
  animation: confirm-fade-in 0.16s ease-out;
}

@keyframes confirm-fade-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .confirm-overlay {
    animation: confirm-fade-in 0.12s ease-out !important;
  }
  .confirm-card {
    animation: none !important;
  }
}
</style>

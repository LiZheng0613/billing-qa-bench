/**
 * 全局确认弹窗：以 Promise 形式替代原生 window.confirm。
 *
 * 用法：
 *   const confirm = useConfirm()
 *   if (!(await confirm({ title: '删除环境', message: '确定删除环境 QA-MOCK 吗？' }))) return
 *
 * 弹窗由 App.vue 中挂载的唯一 <ConfirmDialog> 渲染，因此全站样式与交互一致。
 */
import { reactive } from 'vue'

export type ConfirmTone = 'danger' | 'warning' | 'primary'
export type ConfirmIcon = 'trash' | 'warning'

export interface ConfirmOptions {
  /** 弹窗标题，建议使用动词短语，如「删除环境」 */
  title: string
  /** 主说明文案，通常包含被操作对象的名称 */
  message?: string
  /** 次要说明，用于展示后果 / 注意事项，会以浅色块展示 */
  detail?: string
  /** 确认按钮文案，默认「确认」；删除类建议「删除」 */
  confirmText?: string
  /** 取消按钮文案，默认「取消」 */
  cancelText?: string
  /** 语义色调：danger 删除 / warning 停用与撤销 / primary 普通确认 */
  tone?: ConfirmTone
  /** 图标：trash 删除类，warning 其他破坏性操作 */
  icon?: ConfirmIcon
}

interface ConfirmState {
  open: boolean
  title: string
  message: string
  detail: string
  confirmText: string
  cancelText: string
  tone: ConfirmTone
  icon: ConfirmIcon
  resolve: ((confirmed: boolean) => void) | null
  /** 当前弹窗的唯一标识，用于让 ConfirmDialog 在连续调用时重放入场动画 */
  seq: number
}

const state = reactive<ConfirmState>({
  open: false,
  title: '',
  message: '',
  detail: '',
  confirmText: '确认',
  cancelText: '取消',
  tone: 'danger',
  icon: 'warning',
  resolve: null,
  seq: 0,
})

export function confirmState(): Readonly<ConfirmState> {
  return state
}

/** 是否有确认弹窗处于打开状态（供 BaseModal 等组件避免重复响应 Esc）。 */
export function isConfirmOpen(): boolean {
  return state.open
}

/** 打开确认弹窗并等待用户选择。重复调用会自动关闭上一个弹窗（按取消处理）。 */
export function confirmAction(options: ConfirmOptions): Promise<boolean> {
  if (state.resolve) {
    const pending = state.resolve
    state.resolve = null
    pending(false)
  }
  return new Promise<boolean>((resolve) => {
    state.open = true
    state.seq += 1
    state.title = options.title
    state.message = options.message ?? ''
    state.detail = options.detail ?? ''
    state.confirmText = options.confirmText ?? '确认'
    state.cancelText = options.cancelText ?? '取消'
    state.tone = options.tone ?? 'danger'
    state.icon = options.icon ?? (options.tone === 'danger' ? 'trash' : 'warning')
    state.resolve = resolve
  })
}

/** 关闭弹窗并回传结果，由 ConfirmDialog 调用。 */
export function settleConfirm(confirmed: boolean): void {
  if (!state.open) return
  const resolve = state.resolve
  state.resolve = null
  state.open = false
  resolve?.(confirmed)
}

export function useConfirm(): (options: ConfirmOptions) => Promise<boolean> {
  return confirmAction
}

import { onBeforeUnmount, onMounted } from 'vue'

export interface AutoRefreshOptions {
  /** 轮询间隔（毫秒）或按当前数据动态计算间隔，默认 5000 */
  interval?: number | (() => number)
  /** 返回 true 时跳过本轮刷新（例如当前没有正在执行的记录） */
  shouldSkip?: () => boolean
  /** 浏览器标签页不可见时是否跳过本轮，默认 true */
  skipWhenHidden?: boolean
}

/**
 * 页面级自动刷新：
 * - 进入页面后按固定间隔执行刷新任务；
 * - 标签页切到后台时自动暂停，恢复可见后下一轮立即刷新；
 * - 上一次刷新未结束时跳过本轮，避免请求堆叠；
 * - 页面卸载时自动清理定时器。
 */
export function useAutoRefresh(
  task: () => void | Promise<void>,
  options: AutoRefreshOptions = {},
): void {
  const { interval = 5000, shouldSkip, skipWhenHidden = true } = options
  let timer: number | null = null
  let inFlight = false
  let disposed = false

  function resolveInterval(): number {
    return typeof interval === 'function' ? interval() : interval
  }

  async function run(): Promise<void> {
    if (disposed || inFlight) return
    if (skipWhenHidden && document.visibilityState !== 'visible') return
    if (shouldSkip?.()) return
    inFlight = true
    try {
      await task()
    } catch {
      // 自动刷新保持静默，错误提示由调用方在手动刷新时负责
    } finally {
      inFlight = false
    }
  }

  function schedule(): void {
    if (disposed) return
    timer = window.setTimeout(async () => {
      timer = null
      await run()
      schedule()
    }, Math.max(1000, resolveInterval()))
  }

  function onVisibilityChange(): void {
    // 回到前台时不等下一个周期，立即补一次数据（进行中的请求由 run 自行去重）
    if (document.visibilityState === 'visible') void run()
  }

  onMounted(() => {
    document.addEventListener('visibilitychange', onVisibilityChange)
    schedule()
  })

  onBeforeUnmount(() => {
    disposed = true
    document.removeEventListener('visibilitychange', onVisibilityChange)
    if (timer !== null) {
      window.clearTimeout(timer)
      timer = null
    }
  })
}

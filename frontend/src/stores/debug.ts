import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api'
import { router } from '../router'
import { errorMessage } from '../utils/errors'
import { statusLabel } from '../utils/format'
import { useToastStore } from './toast'

/**
 * 单条调试任务状态：
 * - running  评测接口调用中（跨页面保持，按钮禁用防重复发起）
 * - success  接口正常返回（含判分 FAIL / MAX_TURNS_EXCEEDED 等业务结果）
 * - failed   请求异常（网络错误、HTTP 5xx 等）
 */
export type DebugTaskStatus = 'running' | 'success' | 'failed'

export interface DebugTask {
  id: number
  caseId: number
  caseCode: string
  status: DebugTaskStatus
  startedAt: number
  finishedAt: number | null
  /** 接口耗时（ms），success / failed 时有效 */
  durationMs: number | null
  /** 执行记录 ID，「查看结果」跳转 trace 用 */
  executionId: number | null
  /** 业务执行状态（PASS / FAIL / …），success 时有效 */
  executionStatus: string | null
  passed: boolean | null
  totalScore: number | null
  /** 请求异常时的错误摘要 */
  errorText: string | null
}

let seed = 0

export const useDebugStore = defineStore('debug', () => {
  const tasks = ref<DebugTask[]>([])
  const toast = useToastStore()

  function isRunning(caseId: number): boolean {
    return tasks.value.some((task) => task.caseId === caseId && task.status === 'running')
  }

  function remove(id: number): void {
    tasks.value = tasks.value.filter((task) => task.id !== id)
  }

  function patch(id: number, changes: Partial<DebugTask>): void {
    tasks.value = tasks.value.map((task) => (task.id === id ? { ...task, ...changes } : task))
  }

  /**
   * 发起单条调试。请求生命周期归全局 store 管：
   * - 运行中：状态跨页面保持，同用例不可重复发起；
   * - 完成时仍在发起页 → 保持原有体验（toast + 自动跳转 trace，不出气泡）；
   * - 完成时已离开发起页 → 不跳转，底部胶囊常驻展示结果，仅手动关闭；
   * - 请求异常 → 不跳转，胶囊展示错误摘要。
   */
  async function run(input: {
    id: number
    code: string
    environmentId?: number | null
  }): Promise<void> {
    if (isRunning(input.id)) return
    seed += 1
    const task: DebugTask = {
      id: seed,
      caseId: input.id,
      caseCode: input.code,
      status: 'running',
      startedAt: Date.now(),
      finishedAt: null,
      durationMs: null,
      executionId: null,
      executionStatus: null,
      passed: null,
      totalScore: null,
      errorText: null,
    }
    tasks.value = [...tasks.value, task]

    // 记录发起页面：完成后「仍在原页」才自动跳转，否则只出胶囊提示
    const origin = router.currentRoute.value
    const stillOnOrigin = (): boolean => {
      const current = router.currentRoute.value
      return (
        current.name === origin.name &&
        String(current.params.id ?? '') === String(origin.params.id ?? '')
      )
    }

    toast.info(`正在执行 ${input.code} 单条调试，请稍候…`)

    try {
      const detail = await api.cases.debug(input.id, {
        environment_id: input.environmentId ?? null,
      })
      const finishedAt = Date.now()
      patch(task.id, {
        status: 'success',
        finishedAt,
        durationMs: finishedAt - task.startedAt,
        executionId: detail.id,
        executionStatus: detail.status,
        passed: detail.pass_flag,
        totalScore: detail.total_score,
      })
      if (stillOnOrigin()) {
        toast.success(`调试完成：${statusLabel(detail.status)}，得分 ${detail.total_score ?? '—'}`)
        await router.push({
          name: 'trace',
          query: { execution: String(detail.id), from: 'case' },
        })
        remove(task.id)
      }
    } catch (exc) {
      const finishedAt = Date.now()
      patch(task.id, {
        status: 'failed',
        finishedAt,
        durationMs: finishedAt - task.startedAt,
        errorText: errorMessage(exc),
      })
    }
  }

  return { tasks, isRunning, remove, run }
})

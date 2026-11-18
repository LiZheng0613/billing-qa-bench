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
  /** 发起页面路由（name + params.id）：胶囊展示与完成跳转都以它为基准 */
  originName: string
  originParamsId: string
  /** 用户已收起「调试中」胶囊：仅隐藏运行提示，完成后仍弹出结果胶囊 */
  runningDismissed: boolean
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
  /** 失败追问自检状态（DONE / SKIPPED / ERROR），未开启未自检为 null */
  selfCheckStatus: string | null
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

  /**
   * 用户是否仍停留在该任务的发起页：
   * - 运行中：发起页用按钮「调试中…」自行提示，底部不出胶囊；
   * - 完成时：仍在发起页 → toast + 自动跳转（原有体验），不进胶囊流。
   */
  function isOnOrigin(task: DebugTask): boolean {
    const current = router.currentRoute.value
    return (
      String(current.name ?? '') === task.originName &&
      String(current.params.id ?? '') === task.originParamsId
    )
  }

  function remove(id: number): void {
    tasks.value = tasks.value.filter((task) => task.id !== id)
  }

  function patch(id: number, changes: Partial<DebugTask>): void {
    tasks.value = tasks.value.map((task) => (task.id === id ? { ...task, ...changes } : task))
  }

  /** 收起「调试中」胶囊：调试继续后台执行，完成后仍会弹出结果胶囊 */
  function dismissRunning(id: number): void {
    tasks.value = tasks.value.map((task) =>
      task.id === id && task.status === 'running'
        ? { ...task, runningDismissed: true }
        : task,
    )
  }

  /**
   * 发起单条调试。请求生命周期归全局 store 管：
   * - 运行中：状态跨页面保持，同用例不可重复发起；
   *   留在发起页时不展示底部胶囊，切走页面后才由胶囊展示运行进度；
   * - 完成时仍在发起页 → 保持原有体验（toast + 自动跳转 trace，不出气泡）；
   * - 完成时已离开发起页 → 不跳转，底部胶囊常驻展示结果，仅手动关闭；
   * - 请求异常 → 不跳转，胶囊展示错误摘要。
   */
  async function run(input: {
    id: number
    code: string
    environmentId?: number | null
    /** LLM 裁决判失败时，在同一会话内追问被测 Agent 自检原因 */
    enableSelfCheck?: boolean
  }): Promise<void> {
    if (isRunning(input.id)) return
    seed += 1
    // 记录发起页面：胶囊只在用户离开它之后展示；完成时「仍在原页」才自动跳转
    const origin = router.currentRoute.value
    const task: DebugTask = {
      id: seed,
      caseId: input.id,
      caseCode: input.code,
      originName: String(origin.name ?? ''),
      originParamsId: String(origin.params.id ?? ''),
      runningDismissed: false,
      status: 'running',
      startedAt: Date.now(),
      finishedAt: null,
      durationMs: null,
      executionId: null,
      executionStatus: null,
      passed: null,
      totalScore: null,
      selfCheckStatus: null,
      errorText: null,
    }
    tasks.value = [...tasks.value, task]

    toast.info(`正在执行 ${input.code} 单条调试，请稍候…`)

    try {
      const detail = await api.cases.debug(input.id, {
        environment_id: input.environmentId ?? null,
        enable_self_check: input.enableSelfCheck ?? false,
      })
      const finishedAt = Date.now()
      const selfCheckStatus = detail.self_check?.status ?? null
      patch(task.id, {
        status: 'success',
        finishedAt,
        durationMs: finishedAt - task.startedAt,
        executionId: detail.id,
        executionStatus: detail.status,
        passed: detail.pass_flag,
        totalScore: detail.total_score,
        selfCheckStatus,
      })
      if (isOnOrigin(task)) {
        // 先移除任务再跳转，避免完成瞬间「结果胶囊」在原页面闪现
        const selfCheckNote =
          selfCheckStatus === 'DONE'
            ? '，已追问自检'
            : selfCheckStatus === 'SKIPPED'
              ? '，自检未触发'
              : selfCheckStatus === 'ERROR'
                ? '，自检追问失败'
                : ''
        toast.success(
          `调试完成：${statusLabel(detail.status)}，得分 ${detail.total_score ?? '—'}${selfCheckNote}`,
        )
        remove(task.id)
        await router.push({
          name: 'trace',
          query: {
            execution: String(detail.id),
            from: 'case',
            // 自检成功时直接落在「追问自检」页签，省一次点击
            ...(selfCheckStatus === 'DONE' ? { tab: 'selfcheck' } : {}),
          },
        })
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

  return { tasks, isRunning, isOnOrigin, dismissRunning, remove, run }
})

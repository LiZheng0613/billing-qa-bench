/** 展示层格式化工具。 */

const dateTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

export function formatDateTime(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return dateTimeFormatter.format(date)
}

export function formatTime(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleTimeString('zh-CN', { hour12: false })
}

export function formatMs(value?: number | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  if (value >= 1000) return `${(value / 1000).toFixed(2)} s`
  return `${Math.round(value)} ms`
}

export function formatSeconds(value?: number | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  if (value >= 60) return `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`
  return `${value.toFixed(value >= 10 ? 0 : 1)}s`
}

export function formatNumber(value?: number | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return new Intl.NumberFormat('zh-CN').format(value)
}

export function formatPercent(value?: number | null, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value.toFixed(digits)}%`
}

export function formatScore(value?: number | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toFixed(1)
}

export function truncate(value?: string | null, length = 120): string {
  if (!value) return '—'
  return value.length <= length ? value : `${value.slice(0, length)}…`
}

export function safeJson(value: unknown, space = 2): string {
  if (value === undefined) return 'undefined'
  try {
    return JSON.stringify(value, null, space)
  } catch {
    return String(value)
  }
}

export function parseJsonObject(text: string): Record<string, unknown> {
  const trimmed = text.trim()
  if (!trimmed) return {}
  const parsed = JSON.parse(trimmed) as unknown
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('请输入 JSON 对象（{...}）')
  }
  return parsed as Record<string, unknown>
}

const STATUS_LABELS: Record<string, string> = {
  PASS: '通过',
  FAIL: '失败',
  ERROR: '异常',
  MAX_TURNS_EXCEEDED: '轮次超限',
  REPETITION_ABORT: '复读熔断',
  ABORTED: '已终止',
  RUNNING: '执行中',
  PENDING: '等待中',
  COMPLETED: '已完成',
  FAILED: '执行失败',
  SCHEDULED: '计划中',
  IMMEDIATE: '立即执行',
  DAILY: '每天定时',
  WEEKLY: '每周定时',
  ACTIVE: '启用',
  DISABLED: '停用',
  DRAFT: '草稿',
  UP: '健康',
  DOWN: '异常',
  GOAL_ACHIEVED: '目标达成',
  GIVE_UP: '主动放弃',
  CREATE: '创建',
  UPDATE: '更新',
  IMPORT: '导入',
  ROLLBACK: '回滚',
  BATCH: '批量',
  RETRY: '失败重跑',
  RESUME: '继续执行',
  DEBUG: '单条调试',
  SANDBOX: '沙盒',
  INITIAL: '初始提问',
  SIMULATOR: '模拟用户',
  STATIC: '脚本轮次',
  AVG: '平均分',
  VETO: '一票否决',
  LATEST: '跟随最新',
  PINNED: '锁定版本',
  USER_SIMULATOR: '用户模拟器',
  LLM_JUDGE: 'LLM 裁判',
  CASE_AUTHOR: 'AI 用例编写',
  SELF_CHECK: '失败追问自检',
  SINGLE: '单轮',
  STATIC_MULTI: '静态多轮',
  DYNAMIC_MULTI: '动态多轮',
  SIMULATOR_ROLE: '模拟器模型',
  JUDGE_ROLE: '裁判模型',
  CASE_AUTHOR_ROLE: '用例编写模型',
}

export function statusLabel(status?: string | null): string {
  if (!status) return '—'
  return STATUS_LABELS[status] ?? status
}

export function caseTypeLabel(type?: string | null): string {
  return statusLabel(type)
}

export function templateTypeLabel(type?: string | null): string {
  return statusLabel(type)
}

export function roleLabel(role?: string | null): string {
  if (role === 'SIMULATOR') return '模拟器模型'
  if (role === 'JUDGE') return '裁判模型'
  if (role === 'CASE_AUTHOR') return '用例编写模型'
  return statusLabel(role)
}

export function sourceLabel(source?: string | null): string {
  return statusLabel(source)
}

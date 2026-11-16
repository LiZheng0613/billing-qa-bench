import type { BatchTask, TaskScheduleType } from '../types'

/** 表单里的执行方式（与后端 TaskScheduleType 对应）。 */
export interface ScheduleFormValue {
  type: TaskScheduleType
  /** datetime-local 的值：YYYY-MM-DDTHH:mm */
  scheduledAt: string
  /** HH:mm */
  time: string
  /** 1=周一 … 7=周日 */
  weekdays: number[]
}

export const SCHEDULE_MODE_OPTIONS: Array<{
  value: TaskScheduleType
  label: string
  hint: string
}> = [
  { value: 'IMMEDIATE', label: '立即执行', hint: '创建后马上开跑' },
  { value: 'SCHEDULED', label: '指定时间', hint: '到点执行一次' },
  { value: 'DAILY', label: '每天定时', hint: '每天同一时刻' },
  { value: 'WEEKLY', label: '每周定时', hint: '每周固定几天' },
]

export const WEEKDAY_OPTIONS: Array<{ value: number; label: string }> = [
  { value: 1, label: '周一' },
  { value: 2, label: '周二' },
  { value: 3, label: '周三' },
  { value: 4, label: '周四' },
  { value: 5, label: '周五' },
  { value: 6, label: '周六' },
  { value: 7, label: '周日' },
]

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

export function toDatetimeLocal(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function defaultSchedule(): ScheduleFormValue {
  const next = new Date(Date.now() + 60 * 60 * 1000)
  next.setSeconds(0, 0)
  return {
    type: 'IMMEDIATE',
    scheduledAt: toDatetimeLocal(next),
    time: '09:00',
    weekdays: [1, 2, 3, 4, 5],
  }
}

export function normalizeSchedule(value: ScheduleFormValue): ScheduleFormValue {
  return {
    type: value.type,
    scheduledAt: value.scheduledAt || defaultSchedule().scheduledAt,
    time: value.time || '09:00',
    weekdays: [...new Set(value.weekdays)].sort((a, b) => a - b),
  }
}

export function weekdayLabels(days: number[] | null | undefined): string {
  const list = (days ?? []).map(
    (day) => WEEKDAY_OPTIONS.find((item) => item.value === day)?.label ?? `周${day}`,
  )
  return list.join('、')
}

export function scheduleSummary(value: ScheduleFormValue): string {
  switch (value.type) {
    case 'SCHEDULED':
      return value.scheduledAt
        ? `${value.scheduledAt.replace('T', ' ')} 执行一次`
        : '请选择执行时间'
    case 'DAILY':
      return `每天 ${value.time || '--:--'} 执行`
    case 'WEEKLY':
      return `${weekdayLabels(value.weekdays) || '未选择星期'} ${value.time || '--:--'} 执行`
    default:
      return '创建后立即开始执行'
  }
}

export function scheduleValidationError(value: ScheduleFormValue): string | null {
  if (value.type === 'SCHEDULED') {
    if (!value.scheduledAt) return '请选择指定执行时间'
    const moment = new Date(value.scheduledAt)
    if (Number.isNaN(moment.getTime())) return '执行时间格式不正确'
    if (moment.getTime() <= Date.now()) return '指定执行时间必须晚于当前时间'
  }
  if (value.type === 'DAILY' || value.type === 'WEEKLY') {
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(value.time)) return '执行时刻格式需为 HH:MM'
  }
  if (value.type === 'WEEKLY' && value.weekdays.length === 0) {
    return '每周定时至少选择一个星期'
  }
  return null
}

export function schedulePayload(value: ScheduleFormValue): {
  schedule_type: TaskScheduleType
  scheduled_at: string | null
  schedule_time: string | null
  schedule_weekdays: number[]
} {
  if (value.type === 'SCHEDULED') {
    return {
      schedule_type: value.type,
      scheduled_at: value.scheduledAt,
      schedule_time: null,
      schedule_weekdays: [],
    }
  }
  if (value.type === 'DAILY' || value.type === 'WEEKLY') {
    return {
      schedule_type: value.type,
      scheduled_at: null,
      schedule_time: value.time,
      schedule_weekdays: value.type === 'WEEKLY' ? [...value.weekdays].sort((a, b) => a - b) : [],
    }
  }
  return {
    schedule_type: 'IMMEDIATE',
    scheduled_at: null,
    schedule_time: null,
    schedule_weekdays: [],
  }
}

/** 任务行上展示的执行方式描述。 */
export function taskScheduleLabel(task: BatchTask): string {
  switch (task.schedule_type) {
    case 'SCHEDULED':
      return task.scheduled_at
        ? `指定时间 ${task.scheduled_at.slice(0, 16).replace('T', ' ')}`
        : '指定时间'
    case 'DAILY':
      return `每天 ${task.schedule_time ?? '--:--'}`
    case 'WEEKLY':
      return `每${weekdayLabels(task.schedule_weekdays)} ${task.schedule_time ?? '--:--'}`
    default:
      return '立即执行'
  }
}

export function isPlanTask(task: BatchTask | null | undefined): boolean {
  return Boolean(task && task.schedule_type !== 'IMMEDIATE')
}

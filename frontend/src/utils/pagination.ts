const STORAGE_PREFIX = 'bq.pageSize.'

/** 读取列表的每页条数记忆；非法或不在候选值内时回退到默认值。 */
export function readPageSize(key: string, fallback: number, options?: number[]): number {
  try {
    const raw = localStorage.getItem(STORAGE_PREFIX + key)
    if (!raw) return fallback
    const parsed = Number(raw)
    if (!Number.isInteger(parsed) || parsed <= 0) return fallback
    if (options && !options.includes(parsed)) return fallback
    return parsed
  } catch {
    // 隐私模式或存储被禁用时按默认值处理
    return fallback
  }
}

/** 记住列表的每页条数，失败时静默忽略。 */
export function savePageSize(key: string, size: number): void {
  try {
    localStorage.setItem(STORAGE_PREFIX + key, String(size))
  } catch {
    // 忽略存储失败，不影响分页本身
  }
}

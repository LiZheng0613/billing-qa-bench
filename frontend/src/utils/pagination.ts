const STORAGE_PREFIX = 'bq.pageSize.'

/** 全量分页拉取的每批并行页数：兼顾延迟与浏览器同源连接池（6 连接）压力。 */
const PAGE_CHUNK = 8

/**
 * 分页拉取全量列表：首页先行确定总页数，剩余页按批并行（每批 8 页）。
 * 逐页串行是 N 个往返；一次全发又会在大库时挤占连接池、形成查询风暴。
 */
export async function fetchAllPages<T>(
  fetchPage: (page: number) => Promise<{ items: T[]; total: number }>,
  pageSize: number,
): Promise<T[]> {
  const first = await fetchPage(1)
  const all = [...first.items]
  const totalPages = Math.ceil(first.total / pageSize)
  for (let start = 2; start <= totalPages; start += PAGE_CHUNK) {
    const count = Math.min(PAGE_CHUNK, totalPages - start + 1)
    const pages = await Promise.all(
      Array.from({ length: count }, (_, index) => fetchPage(start + index)),
    )
    pages.forEach((page) => all.push(...page.items))
  }
  return all
}

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

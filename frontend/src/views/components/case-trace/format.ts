/** 证据渲染用的小工具：JSON 高亮、字节数、长度截断。 */

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

export function textOf(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

export function pretty(value: unknown): string {
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2) ?? ''
  } catch {
    return String(value)
  }
}

/** 极简 JSON 高亮：先转义再按 token 着色，输出可安全交给 v-html。 */
export function highlightJson(value: unknown): string {
  const raw = pretty(value)
  if (!raw) return '<span class="j-nul">—</span>'
  const escaped = escapeHtml(raw)
  return escaped.replace(
    /("(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(?:true|false)\b|\bnull\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,
    (match) => {
      if (match.startsWith('"')) {
        if (match.trimEnd().endsWith(':')) {
          return `<span class="j-key">${match}</span>`
        }
        return `<span class="j-str">${match}</span>`
      }
      if (match === 'true' || match === 'false') return `<span class="j-bool">${match}</span>`
      if (match === 'null') return `<span class="j-nul">${match}</span>`
      return `<span class="j-num">${match}</span>`
    },
  )
}

export function byteSize(value: unknown): string {
  const text = typeof value === 'string' ? value : pretty(value)
  if (!text) return '0 B'
  try {
    return `${new Blob([text]).size} B`
  } catch {
    return `${text.length} B`
  }
}

export function lineCount(value: unknown): number {
  const text = typeof value === 'string' ? value : pretty(value)
  if (!text) return 0
  return text.split('\n').length
}

export function shortHash(value: string | null | undefined, keep = 10): string {
  if (!value) return '—'
  return value.length <= keep + 4 ? value : `${value.slice(0, keep)}…${value.slice(-4)}`
}

export function statusText(status: string | null | undefined): string {
  const map: Record<string, string> = {
    DONE: '已追问',
    SKIPPED: '未触发',
    ERROR: '追问失败',
    PENDING: '等待中',
    RUNNING: '执行中',
    SUCCEEDED: '成功',
    FAILED: '失败',
    PASSED: '通过',
  }
  if (!status) return '—'
  return map[status] ?? status
}

export function inputSourceText(source: string | null | undefined): string {
  const map: Record<string, string> = {
    SIMULATOR: '模拟用户',
    STATIC: '用例脚本',
    INITIAL: '用例首问',
    MANUAL: '人工输入',
  }
  if (!source) return '—'
  return map[source] ?? source
}

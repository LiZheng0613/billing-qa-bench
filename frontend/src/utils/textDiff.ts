export type DiffSegmentKind = 'plain' | 'delete' | 'insert'

export interface DiffSegment {
  text: string
  kind: DiffSegmentKind
}

export type DiffRowKind = 'same' | 'change' | 'missing' | 'extra'

export interface DiffRow {
  kind: DiffRowKind
  left: DiffSegment[]
  right: DiffSegment[]
}

export interface DiffResult {
  rows: DiffRow[]
  changes: number
  extras: number
  missing: number
  hasDiff: boolean
}

function splitSentences(text: string): string[] {
  const normalized = text.replace(/\r\n/g, '\n').trim()
  if (!normalized) return []
  const parts = normalized.match(/[^。！？!?；;\n]+[。！？!?；;\n]?/g) ?? [normalized]
  return parts.map((part) => part.trim()).filter(Boolean)
}

function normalize(text: string): string {
  return text
    .replace(/\s+/g, '')
    .replace(/[，,]/g, '，')
    .replace(/[：:]/g, '：')
    .toLowerCase()
}

function lcsPairs(left: string[], right: string[]): Array<[number, number]> {
  const n = left.length
  const m = right.length
  const dp: number[][] = Array.from({ length: n + 1 }, () =>
    new Array<number>(m + 1).fill(0),
  )
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      dp[i][j] =
        normalize(left[i]) === normalize(right[j])
          ? dp[i + 1][j + 1] + 1
          : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const pairs: Array<[number, number]> = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (normalize(left[i]) === normalize(right[j])) {
      pairs.push([i, j])
      i += 1
      j += 1
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      i += 1
    } else {
      j += 1
    }
  }
  return pairs
}

function plain(text: string): DiffSegment[] {
  return text ? [{ text, kind: 'plain' }] : []
}

function changeRow(left: string, right: string): DiffRow {
  const leftText = left.trim()
  const rightText = right.trim()
  if (!leftText && !rightText) {
    return { kind: 'same', left: [], right: [] }
  }
  if (!leftText) {
    return {
      kind: 'extra',
      left: [],
      right: [{ text: rightText, kind: 'insert' }],
    }
  }
  if (!rightText) {
    return {
      kind: 'missing',
      left: [{ text: leftText, kind: 'delete' }],
      right: [],
    }
  }

  // 公共前后缀裁剪，中间不一致的部分分别标记为删除 / 新增。
  let start = 0
  const maxStart = Math.min(leftText.length, rightText.length)
  while (start < maxStart && leftText[start] === rightText[start]) start += 1

  let end = 0
  const maxEnd = Math.min(leftText.length, rightText.length) - start
  while (
    end < maxEnd &&
    leftText[leftText.length - 1 - end] === rightText[rightText.length - 1 - end]
  ) {
    end += 1
  }

  const prefix = leftText.slice(0, start)
  const suffix = leftText.slice(leftText.length - end)
  const leftMiddle = leftText.slice(start, leftText.length - end)
  const rightMiddle = rightText.slice(start, rightText.length - end)

  const leftSegments: DiffSegment[] = []
  if (prefix) leftSegments.push({ text: prefix, kind: 'plain' })
  if (leftMiddle) leftSegments.push({ text: leftMiddle, kind: 'delete' })
  if (suffix) leftSegments.push({ text: suffix, kind: 'plain' })

  const rightSegments: DiffSegment[] = []
  if (prefix) rightSegments.push({ text: prefix, kind: 'plain' })
  if (rightMiddle) rightSegments.push({ text: rightMiddle, kind: 'insert' })
  if (suffix) rightSegments.push({ text: suffix, kind: 'plain' })

  return {
    kind: 'change',
    left: leftSegments,
    right: rightSegments,
  }
}

function sameRow(text: string): DiffRow {
  return { kind: 'same', left: plain(text), right: plain(text) }
}

export function diffText(expected: string | null, actual: string | null): DiffResult {
  const leftText = (expected ?? '').trim()
  const rightText = (actual ?? '').trim()
  const rows: DiffRow[] = []

  if (!leftText && !rightText) {
    return { rows, changes: 0, extras: 0, missing: 0, hasDiff: false }
  }

  const left = splitSentences(leftText)
  const right = splitSentences(rightText)
  const pairs = lcsPairs(left, right)
  let leftCursor = 0
  let rightCursor = 0

  const flushGap = (leftEnd: number, rightEnd: number): void => {
    const leftGap = left.slice(leftCursor, leftEnd)
    const rightGap = right.slice(rightCursor, rightEnd)
    const pairCount = Math.min(leftGap.length, rightGap.length)
    for (let index = 0; index < pairCount; index += 1) {
      const row = changeRow(leftGap[index], rightGap[index])
      rows.push(row.kind === 'same' ? sameRow(leftGap[index]) : row)
    }
    for (let index = pairCount; index < leftGap.length; index += 1) {
      rows.push({
        kind: 'missing',
        left: [{ text: leftGap[index], kind: 'delete' }],
        right: [],
      })
    }
    for (let index = pairCount; index < rightGap.length; index += 1) {
      rows.push({
        kind: 'extra',
        left: [],
        right: [{ text: rightGap[index], kind: 'insert' }],
      })
    }
    leftCursor = leftEnd
    rightCursor = rightEnd
  }

  pairs.forEach(([leftIndex, rightIndex]) => {
    flushGap(leftIndex, rightIndex)
    rows.push(sameRow(left[leftIndex]))
    leftCursor = leftIndex + 1
    rightCursor = rightIndex + 1
  })
  flushGap(left.length, right.length)

  let changes = 0
  let extras = 0
  let missing = 0
  rows.forEach((row) => {
    if (row.kind === 'change') changes += 1
    else if (row.kind === 'extra') extras += 1
    else if (row.kind === 'missing') missing += 1
  })
  return {
    rows,
    changes,
    extras,
    missing,
    hasDiff: changes + extras + missing > 0,
  }
}

export function visibleDiffRows(
  rows: DiffRow[],
  onlyDiff: boolean,
): DiffRow[] {
  if (!onlyDiff) return rows
  return rows.filter((row) => row.kind !== 'same')
}

export function diffGlyph(kind: DiffRowKind): string {
  if (kind === 'change') return '≠'
  if (kind === 'extra') return '+'
  if (kind === 'missing') return '-'
  return '='
}

/* ------------------------------------------------------------------ */
/* 动态多轮：按实体（账期 / 金额 / 笔数 / 手机号）对照，避免整段飘红 */
/* ------------------------------------------------------------------ */

function uniqueStrings(values: string[]): string[] {
  return [...new Set(values)]
}

function extractAmounts(text: string): string[] {
  const values: string[] = []
  const patterns = [/[¥￥]\s*(\d+(?:\.\d+)?)/g, /(\d+(?:\.\d+)?)\s*元/g]
  patterns.forEach((pattern) => {
    let match: RegExpExecArray | null
    while ((match = pattern.exec(text)) !== null) {
      const value = Number(match[1])
      if (Number.isFinite(value)) values.push(value.toFixed(2))
    }
  })
  return uniqueStrings(values)
}

function extractDates(text: string): string[] {
  const values: string[] = []
  const pattern = /(20\d{2})\s*[-/年.]\s*(\d{1,2})(?:\s*[-/月.]\s*(\d{1,2}))?/g
  let match: RegExpExecArray | null
  while ((match = pattern.exec(text)) !== null) {
    const year = match[1]
    const month = String(Number(match[2])).padStart(2, '0')
    const day = match[3] ? `-${String(Number(match[3])).padStart(2, '0')}` : ''
    values.push(`${year}-${month}${day}`)
  }
  return uniqueStrings(values)
}

function extractCounts(text: string): string[] {
  const values: string[] = []
  const pattern = /共\s*(\d+)\s*(?:项|笔|个|条)/g
  let match: RegExpExecArray | null
  while ((match = pattern.exec(text)) !== null) {
    values.push(String(Number(match[1])))
  }
  return uniqueStrings(values)
}

function extractPhones(text: string): string[] {
  return uniqueStrings(text.match(/1[3-9]\d{9}/g) ?? [])
}

function entityRows(
  label: string,
  expected: string[],
  actual: string[],
  format: (value: string) => string,
): DiffRow[] {
  const rows: DiffRow[] = []
  const total = Math.max(expected.length, actual.length)
  for (let index = 0; index < total; index += 1) {
    const left = expected[index]
    const right = actual[index]
    if (left && right) {
      rows.push({
        kind: left === right ? 'same' : 'change',
        left: [{ text: `${label} ${format(left)}`, kind: 'plain' }],
        right: [
          {
            text: `${label} ${format(right)}`,
            kind: left === right ? 'plain' : 'insert',
          },
        ],
      })
    } else if (left) {
      rows.push({
        kind: 'missing',
        left: [{ text: `${label} ${format(left)}`, kind: 'delete' }],
        right: [],
      })
    } else if (right) {
      rows.push({
        kind: 'extra',
        left: [],
        right: [{ text: `${label} ${format(right)}`, kind: 'insert' }],
      })
    }
  }
  return rows
}

/** 动态多轮最终答案：按实体对齐；没有可识别实体时返回 null，调用方回退逐句 diff。 */
export interface EntityDiffOptions {
  amountLabel?: string
  countLabel?: string
  dateLabel?: string
  phoneLabel?: string
}

export function entityDiff(
  expected: string,
  actual: string,
  options: EntityDiffOptions = {},
): DiffResult | null {
  const amountLabel = options.amountLabel || '金额'
  const countLabel = options.countLabel || '费用项数'
  const dateLabel = options.dateLabel || '账期'
  const phoneLabel = options.phoneLabel || '手机号'
  const rows = [
    ...entityRows(dateLabel, extractDates(expected), extractDates(actual), (value) => value),
    ...entityRows(amountLabel, extractAmounts(expected), extractAmounts(actual), (value) => `¥${value}`),
    ...entityRows(countLabel, extractCounts(expected), extractCounts(actual), (value) => value),
    ...entityRows(phoneLabel, extractPhones(expected), extractPhones(actual), (value) => value),
  ]
  if (!rows.length) return null
  let changes = 0
  let extras = 0
  let missing = 0
  rows.forEach((row) => {
    if (row.kind === 'change') changes += 1
    else if (row.kind === 'extra') extras += 1
    else if (row.kind === 'missing') missing += 1
  })
  return { rows, changes, extras, missing, hasDiff: changes + extras + missing > 0 }
}

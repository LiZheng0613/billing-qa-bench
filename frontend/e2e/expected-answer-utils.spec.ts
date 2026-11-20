import { expect, test } from '@playwright/test'
import { diffText, entityDiff, visibleDiffRows } from '../src/utils/textDiff'

test('diffText：按句对齐并高亮金额差异', () => {
  const result = diffText(
    '账单总额 ¥268.50，共 4 项费用。',
    '账单总额 ¥286.50，共 4 项费用。',
  )
  expect(result.changes).toBe(1)
  expect(result.extras).toBe(0)
  expect(result.missing).toBe(0)

  const changed = result.rows.find((row) => row.kind === 'change')
  expect(changed).toBeTruthy()
  const left = changed?.left.map((segment) => segment.text).join('') ?? ''
  const right = changed?.right.map((segment) => segment.text).join('') ?? ''
  expect(left).toContain('68.50')
  expect(right).toContain('86.50')
})

test('diffText：实际多出的句子标记为扩展内容', () => {
  const result = diffText(
    '账单总额 ¥268.50。',
    '账单总额 ¥268.50。如需明细，我可以继续为您查询。',
  )
  expect(result.changes).toBe(0)
  expect(result.extras).toBe(1)
  expect(result.rows.filter((row) => row.kind === 'extra')).toHaveLength(1)
})

test('entityDiff：动态多轮按账期 / 金额 / 项数 / 手机号对齐', () => {
  const result = entityDiff(
    '最终应给出手机号 13800138000 在 2026-10 的账单总额 ¥268.50，共 4 项费用。',
    '您本月（2026-11）的账单总额为 ¥298.00，共 5 项费用。',
    { amountLabel: '账单总额', countLabel: '费用项数' },
  )
  expect(result).toBeTruthy()
  expect(result?.rows.map((row) => row.kind)).toEqual([
    'change',
    'change',
    'change',
    'missing',
  ])
  const leftText = result?.rows.map((row) =>
    row.left.map((segment) => segment.text).join(''),
  )
  expect(leftText).toContain('账期 2026-10')
  expect(leftText).toContain('账单总额 ¥268.50')
  expect(leftText).toContain('费用项数 4')
  expect(leftText).toContain('手机号 13800138000')
})

test('visibleDiffRows：只看差异时过滤一致行', () => {
  const rows = [
    { kind: 'same' as const, left: [{ text: 'a', kind: 'plain' as const }], right: [{ text: 'a', kind: 'plain' as const }] },
    { kind: 'change' as const, left: [{ text: 'b', kind: 'plain' as const }], right: [{ text: 'c', kind: 'insert' as const }] },
  ]
  expect(visibleDiffRows(rows, true)).toHaveLength(1)
  expect(visibleDiffRows(rows, false)).toHaveLength(2)
})

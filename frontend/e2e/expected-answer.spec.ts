import { expect, test } from '@playwright/test'
import { installExpectedAnswerMock } from './expected-answer.fixtures'

/**
 * 执行详情 · 期望答案对照模式回归。
 *
 * 运行前提：前端 dev server 已启动（默认 http://127.0.0.1:5173）。
 * 用例通过 page.route 注入固定执行数据，不需要后端 / 数据库 / mock LLM。
 */
test.beforeEach(async ({ page }) => {
  await installExpectedAnswerMock(page)
})

test('默认进入对话流，切换对照模式后展示单轮期望答案与差异', async ({ page }) => {
  await page.goto('/trace?execution=10231')

  await expect(page.getByRole('button', { name: '对话流' })).toHaveAttribute(
    'aria-pressed',
    'true',
  )
  await expect(page.getByRole('button', { name: '对照模式' })).toHaveAttribute(
    'aria-pressed',
    'false',
  )
  await expect(page.getByText('用户提问')).toBeVisible()
  await expect(page.getByText(/期望答案/)).toHaveCount(0)

  await page.getByRole('button', { name: '对照模式' }).click()

  await expect(page.getByText(/期望答案/).first()).toBeVisible()
  await expect(page.getByText('¥268.50').first()).toBeVisible()
  await expect(page.getByText('¥286.50').first()).toBeVisible()
  await expect(page.getByText('Must 2/3 命中')).toBeVisible()
  await expect(page.getByText('未命中').first()).toBeVisible()

  // 只看差异：没有 same 行时仍保留差异行。
  await page.getByRole('checkbox', { name: '只看差异' }).check()
  await expect(page.getByText('¥268.50').first()).toBeVisible()
})

test('静态多轮：切换轮次、规则生成提示与会话级期望', async ({ page }) => {
  await page.goto('/trace?execution=10232')
  await page.getByRole('button', { name: '对照模式' }).click()

  await expect(page.getByRole('button', { name: /T2 未通过/ })).toHaveClass(/active/)
  await expect(page.getByText('轮次规则校验 · T2')).toBeVisible()
  await expect(page.getByText('规则生成').first()).toBeVisible()

  const activeDiff = page.locator('.diff-body').first()
  await expect(activeDiff.getByText('必须包含 超套').first()).toBeVisible()

  await page.getByRole('checkbox', { name: '只看差异' }).check()
  await expect(activeDiff.getByText('必须包含 超套')).toHaveCount(0)

  await page.getByRole('button', { name: /T1 通过/ }).click()
  await expect(page.getByText('轮次规则校验 · T1')).toBeVisible()
  await expect(page.getByText('会话级期望')).toBeVisible()
  await expect(page.getByText(/账单总额 ¥268\.50.*命中/)).toBeVisible()
})

test('动态多轮：设计/轨迹对照、隐藏信息释放与实体级最终答案差异', async ({ page }) => {
  await page.goto('/trace?execution=10233')
  await page.getByRole('button', { name: '对照模式' }).click()

  await expect(page.getByText('测试设计（期望）')).toBeVisible()
  await expect(page.getByText('执行轨迹（实际）')).toBeVisible()
  await expect(page.getByText('隐藏信息 1/2 已释放')).toBeVisible()
  await expect(page.getByText('未释放：period')).toBeVisible()
  await expect(page.getByText('未澄清账期即作答').first()).toBeVisible()

  await expect(page.getByText('账单总额 ¥268.50', { exact: true })).toBeVisible()
  await expect(page.getByText('账单总额 ¥298.00', { exact: true })).toBeVisible()
  await expect(page.getByText('费用项数 4', { exact: true })).toBeVisible()
  await expect(page.getByText('费用项数 5', { exact: true })).toBeVisible()
  await expect(page.getByText('Must 0/3 命中')).toBeVisible()

  // 只看关键节点：关闭后仍展示完整三轮轨迹。
  await page.getByRole('checkbox', { name: '只看关键节点' }).uncheck()
  await expect(page.locator('.tl-item')).toHaveCount(3)
})

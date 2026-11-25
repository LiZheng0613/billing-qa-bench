import { expect, test } from '@playwright/test'
import { installExpectedAnswerMock } from './expected-answer.fixtures'

/**
 * 执行详情 · 期望答案对照模式回归。
 *
 * 前提：前端 dev server 已启动（默认 http://127.0.0.1:5173）。
 * 数据通过 page.route 注入固定执行快照，不需要后端 / 数据库 / mock LLM。
 */
test.beforeEach(async ({ page }) => {
  await installExpectedAnswerMock(page)
})

test('默认进入对话流，切换对照模式后展示单轮期望答案与差异', async ({ page }) => {
  await page.goto('/trace?execution=10231')

  await expect(page.getByRole('button', { name: '对话', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  )
  await expect(page.getByRole('button', { name: '对照', exact: true })).toHaveAttribute(
    'aria-pressed',
    'false',
  )
  await expect(page.getByText(/请帮我查一下手机号 13800138000/).first()).toBeVisible()
  await expect(page.getByText(/期望回复/)).toHaveCount(0)

  await page.getByRole('button', { name: '对照', exact: true }).click()

  await expect(page.getByText(/与期望一致/).first()).toBeVisible()
  await expect(page.locator('.cmpcard')).toHaveCount(1)
  await expect(page.getByText('期望回复', { exact: false }).first()).toBeVisible()
  await expect(page.getByText('268.5', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('286.5', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('差异', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('+18', { exact: true }).first()).toBeVisible()
})

test('静态多轮：逐轮对照与规则命中、数值差异', async ({ page }) => {
  await page.goto('/trace?execution=10232')
  await page.getByRole('button', { name: '对照', exact: true }).click()

  await expect(page.locator('.cmpcard')).toHaveCount(2)

  const first = page.locator('.cmpcard').nth(0)
  await expect(first.getByText('T1', { exact: true })).toBeVisible()
  await expect(first.getByText('账单总额').first()).toBeVisible()
  await expect(first.getByText('费用项数').first()).toBeVisible()

  const second = page.locator('.cmpcard').nth(1)
  await expect(second.getByText('T2', { exact: true })).toBeVisible()
  await expect(second.getByText('超套费').first()).toBeVisible()
  await expect(second.getByText('27', { exact: true }).first()).toBeVisible()
  await expect(second.getByText('72', { exact: true }).first()).toBeVisible()
  await expect(second.getByText('+45', { exact: true }).first()).toBeVisible()
  await expect(second.getByText('必须包含', { exact: false }).first()).toBeVisible()
})

test('动态多轮：三轮对照、隐藏信息释放与实体级最终答案差异', async ({ page }) => {
  await page.goto('/trace?execution=10233')
  await page.getByRole('button', { name: '对照', exact: true }).click()

  await expect(page.locator('.cmpcard')).toHaveCount(3)
  await expect(page.getByText(/未判定/).first()).toBeVisible()
  await expect(page.locator('.cmpcard').nth(0).getByText('T1', { exact: true })).toBeVisible()
  await expect(page.locator('.cmpcard').nth(1).getByText('T2', { exact: true })).toBeVisible()
  await expect(page.locator('.cmpcard').nth(2).getByText('T3', { exact: true })).toBeVisible()

  await expect(page.getByText('268.5', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('298', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('+29.5', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('4', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('5', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('+1', { exact: true }).first()).toBeVisible()
})

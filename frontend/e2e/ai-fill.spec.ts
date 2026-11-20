import { expect, test, type Page } from '@playwright/test'

const AI_DRAFT_STORAGE_KEY = 'billing-qa:ai-draft:v1'

async function seedAuth(page: Page): Promise<void> {
  const response = await page.request.post('/api/v1/auth/login', {
    data: { username: 'admin', password: 'admin123' },
  })
  expect(response.ok()).toBeTruthy()
  const { access_token: token } = await response.json()
  await page.addInitScript((value: string) => {
    localStorage.setItem('billing_qa_token', value)
  }, token)
}

async function api(page: Page, path: string, method = 'GET') {
  const token = await page.evaluate(() => localStorage.getItem('billing_qa_token'))
  return page.request.fetch(`/api/v1${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}` },
  })
}

async function openCreateDrawer(page: Page): Promise<void> {
  await page.goto('/cases')
  await page.getByRole('button', { name: '新建用例' }).first().click()
  await expect(page.getByRole('heading', { name: '新建用例' })).toBeVisible()
}

async function openAiPanel(page: Page): Promise<void> {
  const beta = page.getByText('BETA', { exact: true })
  // 草稿恢复时面板会自己展开，这里只在未展开时点开，避免误关。
  if (!(await beta.isVisible().catch(() => false))) {
    await page.getByRole('button', { name: 'AI 帮我填' }).first().click()
  }
  // 输入态与草稿回显态都带 BETA 标识。
  await expect(beta).toBeVisible()
}

async function expectApplied(page: Page): Promise<void> {
  await expect(page.getByText(/已应用 AI 草稿/).first()).toBeVisible()
}

async function generateDraft(page: Page, prompt: string): Promise<void> {
  await page.locator('.absolute.right-4 textarea').first().fill(prompt)
  await page.getByRole('button', { name: '生成草稿', exact: true }).click()
  await expect(page.getByText('将回填').first()).toBeVisible({ timeout: 30_000 })
}

test('未登录访问会跳转登录页，登录后进入用例资产', async ({ page }) => {
  await page.goto('/cases')
  await expect(page).toHaveURL(/\/login/)
  await page.locator('#username').fill('admin')
  await page.locator('#password').fill('admin123')
  await page.getByRole('button', { name: '进入控制台' }).click()
  await expect(page.getByText('用例资产', { exact: true }).first()).toBeVisible()
  await expect(page).toHaveURL(/\/cases/)
})

async function deleteCaseByCode(page: Page, code: string): Promise<void> {
  const listed = await api(page, `/cases?keyword=${encodeURIComponent(code)}`)
  const body = await listed.json()
  for (const item of body.items as { id: number; code: string }[]) {
    if (item.code === code) {
      await api(page, `/cases/${item.id}`, 'DELETE')
    }
  }
}

async function findCaseId(page: Page, code: string): Promise<number> {
  const listed = await api(page, `/cases?keyword=${encodeURIComponent(code)}`)
  const body = await listed.json()
  const item = (body.items as { id: number; code: string }[]).find((it) => it.code === code)
  expect(item).toBeTruthy()
  return item!.id
}

test('新建单轮：应用 AI 草稿并保存，未补项写入用例备注', async ({ page }) => {
  await seedAuth(page)
  await openCreateDrawer(page)
  await openAiPanel(page)
  await generateDraft(
    page,
    '帮我生成一条单轮用例：本机号码在 2026-10 的账单总额，客服引导查看账单，不要编造金额',
  )
  // 取数计划虽然只在契约模式落库，但草稿面板应展示 AI 生成的能力编码。
  await expect(page.getByText('取数能力', { exact: true })).toBeVisible()
  await expect(page.getByText(/bill_query/).first()).toBeVisible()
  await expect(page.getByText('请求变量', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('4 维规则', { exact: true }).first()).toBeVisible()

  await page.getByRole('button', { name: /应用 \d+ 项变更/ }).click()
  await expectApplied(page)

  const code = await page.getByPlaceholder('CASE-BILL-001').inputValue()
  expect(code).toMatch(/^AI-\d{8}-\d{3}$/)
  const name = await page.getByPlaceholder('账单金额与明细一致性校验').inputValue()
  expect(name.length).toBeGreaterThan(0)

  try {
    await page.getByRole('button', { name: '创建用例', exact: true }).click()
    await expect(page.getByRole('heading', { name: '新建用例' })).toBeHidden({ timeout: 20_000 })

    const listed = await api(page, `/cases?keyword=${encodeURIComponent(code)}`)
    const body = await listed.json()
    const created = body.items.find((item: { code: string }) => item.code === code)
    expect(created).toBeTruthy()
    expect(created.description).toContain('待补充：')
    // 全字段覆盖：请求变量 / 4 维规则 都应随草稿落库。
    expect(created.request_variables?.billing_period).toBe('2026-10')
    expect(created.expected_rules?.dates ?? []).toContain('2026-10')

    // 保存成功后本地草稿应当被清理。
    const stored = await page.evaluate((key) => localStorage.getItem(key), AI_DRAFT_STORAGE_KEY)
    expect(stored).toBeNull()
  } finally {
    // 清理临时用例，保持演示库 57 条基线。
    await deleteCaseByCode(page, code)
  }
})

test('静态多轮：取消勾选一轮后只回填选中轮次', async ({ page }) => {
  await seedAuth(page)
  await openCreateDrawer(page)
  await openAiPanel(page)
  await generateDraft(
    page,
    '帮我生成一条静态多轮用例，两轮：用户问 13800138000 在 2026-10 的账单总额',
  )

  const turnRow = page.locator('div.border-b').filter({ hasText: '多轮脚本' }).first()
  const childBoxes = turnRow.locator('input[type="checkbox"].h-3')
  await expect(childBoxes).toHaveCount(2)

  const apply = page.getByRole('button', { name: /应用 \d+ 项变更/ })
  const before = await apply.textContent()
  await childBoxes.nth(1).uncheck()
  await expect.poll(async () => apply.textContent()).not.toBe(before)
  await apply.click()
  await expectApplied(page)

  // 应用草稿会自动收起 AI 气泡，这里不再手动关闭。
  // 被取消勾选的第二轮不应进入表单。
  const turnInputs = page.getByPlaceholder('本轮用户提问，例如：那含税总额是多少？')
  await expect(turnInputs).toHaveCount(1)
  await expect(turnInputs.first()).toHaveValue(/帮我查一下/)

  await page.getByRole('button', { name: '取消', exact: true }).click()
  await expect(page.getByRole('heading', { name: '新建用例' })).toBeHidden()
})

test('草稿持久化：刷新后恢复，可手动清除', async ({ page }) => {
  await seedAuth(page)
  await openCreateDrawer(page)
  await openAiPanel(page)
  await generateDraft(page, '帮我生成一条单轮用例：本机号码在 2026-10 的账单总额')

  // 关闭抽屉再打开：AI 浮窗不应自动弹出（草稿仍在，按钮上有「有草稿」提示）。
  await page.getByRole('button', { name: '取消', exact: true }).click()
  await page.getByRole('button', { name: '新建用例' }).first().click()
  await expect(page.getByText('BETA', { exact: true })).toBeHidden()

  await page.reload()
  await page.getByRole('button', { name: '新建用例' }).first().click()
  await expect(page.getByText('BETA', { exact: true })).toBeHidden()
  await openAiPanel(page)
  await expect(page.getByText('已恢复刷新前的草稿')).toBeVisible()

  await page.getByRole('button', { name: '清除草稿' }).click()
  await expect
    .poll(() => page.evaluate((key) => localStorage.getItem(key), AI_DRAFT_STORAGE_KEY))
    .toBeNull()

  await page.getByRole('button', { name: '取消', exact: true }).click()
})

test('快捷示例追加而不覆盖已有输入', async ({ page }) => {
  await seedAuth(page)
  await openCreateDrawer(page)
  await openAiPanel(page)

  const textarea = page.locator('.absolute.right-4 textarea').first()
  await textarea.fill('已有描述：先保留我写的内容')
  const example = page.getByRole('button', { name: '账单金额核对', exact: true })
  await example.click()

  await expect(textarea).toHaveValue(/已有描述/)
  await expect(textarea).toHaveValue(/账单金额核对/)
  // 重复点击同一个示例不应重复追加。
  await example.click()
  const value = await textarea.inputValue()
  expect(value.split('账单金额核对').length - 1).toBe(1)

  await page.getByRole('button', { name: '取消', exact: true }).click()
})

test('编辑动态多轮：应用后 max_turns=8，取消不落库', async ({ page }) => {
  await seedAuth(page)
  await page.goto('/cases')
  const caseId = await findCaseId(page, 'DEMO-DYNAMIC-USAGE')
  const beforeResponse = await api(page, `/cases/${caseId}`)
  const before = await beforeResponse.json()
  await page.goto(`/cases/${caseId}`)
  await page.getByRole('button', { name: '编辑', exact: true }).first().click()
  await expect(page.getByRole('heading', { name: /编辑用例/ })).toBeVisible()

  await openAiPanel(page)
  await generateDraft(page, '最多 8 轮就结束，其他保持不变')
  await page.getByRole('button', { name: /应用 \d+ 项变更/ }).click()
  await expectApplied(page)

  await expect(page.locator('input[type="number"]').first()).toHaveValue('8')
  // 动态多轮的语义字段也应被回填（沿用基线用户目标）。
  await expect(
    page.getByPlaceholder(
      '在不直接提供订单号的前提下，通过追问拿到订单 A2024001 的含税金额与明细笔数',
    ),
  ).toHaveValue(before.user_goal)

  await page.getByRole('button', { name: '取消', exact: true }).click()
  await expect(page.getByRole('heading', { name: /编辑用例/ })).toBeHidden()

  const detail = await api(page, `/cases/${caseId}`)
  const unchanged = await detail.json()
  expect(unchanged.max_turns).toBe(before.max_turns)
})

import { expect, test, type Page } from '@playwright/test'

/**
 * 请求变量落库口径回归：
 * - 所有数字（含嵌套对象/数组里的数字）统一保存为字符串，避免长号码丢精度或变科学计数法；
 * - 用例 A 修改并保存后，切到用例 B 不会带出 A 的值，保存 B 也不会反向覆盖 A。
 */

async function seedAuth(page: Page): Promise<void> {
  const response = await page.request.post('/api/v1/auth/login', {
    data: { username: 'admin', password: 'admin123' },
  })
  expect(response.ok()).toBeTruthy()
  const { access_token: token } = await response.json()
  await page.addInitScript((value: string) => {
    localStorage.setItem('billing_qa_token', value)
  }, token)
  await page.goto('/cases')
}

async function api(page: Page, path: string, method = 'GET', data?: unknown) {
  const token = await page.evaluate(() => localStorage.getItem('billing_qa_token'))
  return page.request.fetch(`/api/v1${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(data === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    ...(data === undefined ? {} : { data }),
  })
}

async function createCase(page: Page, payload: Record<string, unknown>) {
  const response = await api(page, '/cases', 'POST', payload)
  expect(response.ok(), await response.text()).toBeTruthy()
  return (await response.json()) as Record<string, any>
}

async function deleteCase(page: Page, id: number | null): Promise<void> {
  if (!id) return
  await api(page, `/cases/${id}`, 'DELETE')
}

async function requestVariableValueInput(page: Page, key: string) {
  const keys = page.locator('input[placeholder="key，如 user_id"]')
  const total = await keys.count()
  for (let index = 0; index < total; index += 1) {
    const input = keys.nth(index)
    if ((await input.inputValue()) === key) {
      return input.locator('xpath=following-sibling::input[1]')
    }
  }
  throw new Error(`未找到请求变量 ${key}`)
}

async function openEdit(page: Page, caseId: number): Promise<void> {
  await page.goto(`/cases/${caseId}`)
  await page.getByRole('button', { name: '编辑', exact: true }).click()
  await expect(page.getByRole('heading', { name: /编辑用例/ })).toBeVisible()
}

async function saveAndWait(page: Page): Promise<void> {
  await page.getByRole('button', { name: '保存新版本', exact: true }).click()
  await expect(page.getByRole('heading', { name: /编辑用例/ })).toBeHidden({ timeout: 20_000 })
}

test('请求变量数字保存为字符串，A 的修改不会带到 B 或覆盖 B', async ({ page }) => {
  await seedAuth(page)
  const suffix = `${Date.now()}`.slice(-9)
  let caseAId: number | null = null
  let caseBId: number | null = null
  const longUserId = '123456789012345678901234'

  try {
    const caseA = await createCase(page, {
      code: `E2E-RV-A-${suffix}`,
      name: '请求变量隔离 A',
      case_type: 'SINGLE',
      priority: 'P2',
      query: 'q',
      request_variables: { userid: '11111111111', custid: 'CA' },
    })
    caseAId = Number(caseA.id)
    const caseB = await createCase(page, {
      code: `E2E-RV-B-${suffix}`,
      name: '请求变量隔离 B',
      case_type: 'SINGLE',
      priority: 'P2',
      query: 'q',
      request_variables: { userid: '22222222222', custid: 'CB' },
    })
    caseBId = Number(caseB.id)

    await openEdit(page, caseAId)
    const aUserid = await requestVariableValueInput(page, 'userid')
    await expect(aUserid).toHaveValue('11111111111')
    await aUserid.fill(longUserId)
    await saveAndWait(page)

    const savedA = (await (await api(page, `/cases/${caseAId}`)).json()) as Record<string, any>
    expect(savedA.request_variables.userid).toBe(longUserId)
    expect(typeof savedA.request_variables.userid).toBe('string')
    const untouchedB = (await (await api(page, `/cases/${caseBId}`)).json()) as Record<string, any>
    expect(untouchedB.request_variables).toEqual({ userid: '22222222222', custid: 'CB' })

    await openEdit(page, caseBId)
    const bUserid = await requestVariableValueInput(page, 'userid')
    await expect(bUserid).toHaveValue('22222222222')
    await saveAndWait(page)

    const afterB = (await (await api(page, `/cases/${caseBId}`)).json()) as Record<string, any>
    expect(afterB.request_variables).toEqual({ userid: '22222222222', custid: 'CB' })
    const afterA = (await (await api(page, `/cases/${caseAId}`)).json()) as Record<string, any>
    expect(afterA.request_variables.userid).toBe(longUserId)
  } finally {
    await deleteCase(page, caseBId)
    await deleteCase(page, caseAId)
  }
})

test('JSON 模式里的数字（含嵌套）也按字符串保存', async ({ page }) => {
  await seedAuth(page)
  const suffix = `${Date.now()}`.slice(-9)
  let caseId: number | null = null

  try {
    const created = await createCase(page, {
      code: `E2E-RV-JSON-${suffix}`,
      name: '请求变量 JSON 数字字符串化',
      case_type: 'SINGLE',
      priority: 'P2',
      query: 'q',
      request_variables: { userid: '11111111111' },
    })
    caseId = Number(created.id)

    await openEdit(page, caseId)
    await page.getByRole('button', { name: 'JSON 模式', exact: true }).click()
    const editor = page.getByPlaceholder('{"user_id": "13800138000", "channel": "app"}')
    await editor.fill(
      '{"userid": 13800138000123456789, "profile": {"level": 3, "tags": [1, 2]}}',
    )
    await saveAndWait(page)

    const saved = (await (await api(page, `/cases/${caseId}`)).json()) as Record<string, any>
    expect(saved.request_variables).toEqual({
      userid: '13800138000123456789',
      profile: { level: '3', tags: ['1', '2'] },
    })
  } finally {
    await deleteCase(page, caseId)
  }
})

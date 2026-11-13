/**
 * 轻量 fetch 封装：统一鉴权、错误结构、查询参数与 401 处理。
 * 后端错误体为 { code, message, path?, detail? }。
 */

const TOKEN_KEY = 'billing_qa_token'
const BASE_URL = '/api/v1'

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly detail: unknown

  constructor(status: number, code: string, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail
  }
}

export type QueryValue = string | number | boolean | null | undefined

export interface RequestOptions {
  query?: Record<string, QueryValue>
  body?: unknown
  formData?: FormData
  signal?: AbortSignal
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null): void {
  if (token) {
    localStorage.setItem(TOKEN_KEY, token)
  } else {
    localStorage.removeItem(TOKEN_KEY)
  }
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const normalized = path.startsWith('/') ? path : `/${path}`
  const url = `${BASE_URL}${normalized}`
  if (!query) return url
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue
    params.append(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${url}?${qs}` : url
}

async function parseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get('content-type') ?? ''
  if (contentType.includes('application/json')) {
    try {
      return await response.json()
    } catch {
      return null
    }
  }
  const text = await response.text()
  return text || null
}

function extractError(status: number, payload: unknown): ApiError {
  if (payload && typeof payload === 'object') {
    const body = payload as Record<string, unknown>
    const code = typeof body.code === 'string' ? body.code : `HTTP_${status}`
    let message =
      typeof body.message === 'string' ? body.message : `请求失败（HTTP ${status}）`
    if (Array.isArray(body.detail) && body.detail.length > 0) {
      const first = body.detail[0] as Record<string, unknown> | undefined
      if (first && typeof first.msg === 'string') {
        const location = Array.isArray(first.loc) ? first.loc.join('.') : ''
        message = location ? `${message}: ${location} ${first.msg}` : `${message}: ${first.msg}`
      }
    }
    return new ApiError(status, code, message, body.detail)
  }
  return new ApiError(status, `HTTP_${status}`, `请求失败（HTTP ${status}）`, payload)
}

export async function request<T>(
  method: string,
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let body: BodyInit | undefined
  if (options.formData) {
    body = options.formData
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.body)
  }

  let response: Response
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers,
      body,
      signal: options.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(0, 'NETWORK_ERROR', '无法连接后端服务，请确认 API 已启动')
  }

  const payload = response.status === 204 ? null : await parseBody(response)
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login') {
      setToken(null)
      window.dispatchEvent(new CustomEvent('auth:expired'))
    }
    throw extractError(response.status, payload)
  }
  return payload as T
}

export const http = {
  get: <T>(path: string, query?: Record<string, QueryValue>, signal?: AbortSignal) =>
    request<T>('GET', path, { query, signal }),
  post: <T>(path: string, body?: unknown, query?: Record<string, QueryValue>) =>
    request<T>('POST', path, { body, query }),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, { body }),
  delete: <T>(path: string) => request<T>('DELETE', path),
  upload: <T>(path: string, formData: FormData) => request<T>('POST', path, { formData }),
}

/** 生成带 token 的 WebSocket 地址（开发环境走 Vite 代理）。 */
export function buildWebSocketUrl(path: string, params: Record<string, string | number> = {}): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) search.append(key, String(value))
  const token = getToken()
  if (token) search.append('token', token)
  const normalized = path.startsWith('/') ? path : `/${path}`
  return `${protocol}//${window.location.host}/api/v1${normalized}?${search.toString()}`
}

/** 下载导出文件（带鉴权头，无法直接用 a[href]）。 */
export async function downloadFile(path: string, filename: string, query?: Record<string, QueryValue>): Promise<void> {
  const token = getToken()
  const response = await fetch(buildUrl(path, query), {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!response.ok) {
    const payload = await parseBody(response)
    throw extractError(response.status, payload)
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

import { ApiError } from '../api/client'

export function errorMessage(exc: unknown, fallback = '操作失败，请稍后重试'): string {
  if (exc instanceof ApiError) return exc.message
  if (exc instanceof Error && exc.message) return exc.message
  if (typeof exc === 'string' && exc) return exc
  return fallback
}

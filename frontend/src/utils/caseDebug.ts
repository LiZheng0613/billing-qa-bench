import type { CasePayload } from '../types'

/**
 * 编辑抽屉 / 调试工作台的表单产物 → 调试执行快照覆盖字段。
 *
 * 后端 `POST /cases/{id}/debug` 会把 override 浅合并到当前版本快照上执行，
 * 因此这里只剔除「不可变标识 / 审计专用」字段，其余（含请求变量、多轮脚本、
 * 隐藏上下文、答案契约、规则硬校验、模板与模型选择等）全部进入试运行快照。
 */
const SNAPSHOT_EXCLUDED = new Set(['code', 'case_type', 'change_summary'])

export function buildDebugSnapshotOverride(payload: CasePayload): Record<string, unknown> {
  const override: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(payload)) {
    if (SNAPSHOT_EXCLUDED.has(key)) continue
    override[key] = value ?? null
  }
  return override
}

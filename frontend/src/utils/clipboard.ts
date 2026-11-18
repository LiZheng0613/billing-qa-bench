/**
 * 复制文本到剪贴板，返回是否成功。
 *
 * 优先使用异步剪贴板 API；它在以下场景会失败（NotAllowedError 等）：
 * - 页面所在文档未聚焦（内嵌浏览器/桌面壳常见）
 * - 非安全上下文（http 且非 localhost）
 * - 剪贴板权限被拒
 * 此时回退到临时 textarea + execCommand('copy')，保证按钮可用。
 */
export async function copyText(text: string): Promise<boolean> {
  if (typeof text !== 'string' || text.length === 0) return false
  if (typeof navigator !== 'undefined' && navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // 继续走兜底方案
    }
  }
  return legacyCopy(text)
}

function legacyCopy(text: string): boolean {
  const textarea = document.createElement('textarea')
  try {
    const previousFocus =
      document.activeElement instanceof HTMLElement ? document.activeElement : null
    const selection = document.getSelection()
    const previousRange =
      selection && selection.rangeCount > 0 ? selection.getRangeAt(0) : null

    textarea.value = text
    textarea.setAttribute('readonly', '')
    textarea.style.position = 'fixed'
    textarea.style.top = '0'
    textarea.style.left = '-9999px'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    textarea.setSelectionRange(0, textarea.value.length)
    const ok = document.execCommand('copy')

    if (previousRange && selection) {
      selection.removeAllRanges()
      selection.addRange(previousRange)
    }
    previousFocus?.focus({ preventScroll: true })
    return ok
  } catch {
    return false
  } finally {
    textarea.remove()
  }
}

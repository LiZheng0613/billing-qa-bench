import { onBeforeUnmount, watch, type Ref } from 'vue'

/**
 * 内层弹层（自定义取时 / 日期选择器等）的 Esc 层级约定。
 *
 * 打开的内层弹层会登记到注册表：外层 BaseModal 收到 Esc 时若发现存在内层弹层，
 * 就不再关闭自身，保证「Esc 先关内层、再关外层」。
 * 使用同步注册表而不是查询 DOM，避免关闭动画期间残留节点造成误判。
 */
const activeLayers = new Set<symbol>()

export function hasOpenEscLayer(): boolean {
  return activeLayers.size > 0
}

/** 绑定弹层开关：open 为 true 时登记，关闭或卸载时注销。 */
export function useEscLayer(open: Ref<boolean>): void {
  const token = Symbol('esc-layer')
  watch(
    open,
    (value) => {
      if (value) activeLayers.add(token)
      else activeLayers.delete(token)
    },
    { immediate: true },
  )
  onBeforeUnmount(() => activeLayers.delete(token))
}

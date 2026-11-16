import { ref } from 'vue'
import { defineStore } from 'pinia'

export interface BreadcrumbItem {
  label: string
  to?: string
}

/**
 * 顶部面包屑的补充层级。
 *
 * 普通页面由路由 meta.breadcrumb + meta.title 自动生成；
 * 单笔执行结果这类需要先加载数据才能确定归属的页面，可在拿到数据后
 * 调用 ``set`` 提供完整路径，``AppShell`` 会优先使用这里的层级。
 */
export const useBreadcrumbStore = defineStore('breadcrumb', () => {
  const trail = ref<BreadcrumbItem[]>([])

  function set(items: BreadcrumbItem[]): void {
    trail.value = items
  }

  function clear(): void {
    trail.value = []
  }

  return { trail, set, clear }
})

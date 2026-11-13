import { onBeforeUnmount, onMounted, watch } from 'vue'
import type { Ref } from 'vue'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption } from 'echarts'

/**
 * 绑定一个 DOM 容器与响应式 option，自动处理 resize 与销毁。
 *
 * 注意：容器可能位于 Modal / Tab 等延迟挂载的节点中，因此除了 option，
 * 还需要监听 container 本身，等容器真正出现后再初始化实例。
 */
export function useEChart(
  container: Ref<HTMLElement | null>,
  option: Ref<EChartsOption | null>,
): void {
  let chart: ECharts | null = null
  let observer: ResizeObserver | null = null
  let observedEl: HTMLElement | null = null

  function dispose(): void {
    observer?.disconnect()
    observer = null
    observedEl = null
    chart?.dispose()
    chart = null
  }

  function render(): void {
    const el = container.value
    if (!el) {
      dispose()
      return
    }
    if (!chart || observedEl !== el) {
      dispose()
      chart = echarts.init(el, undefined, { renderer: 'canvas' })
      observedEl = el
      observer = new ResizeObserver(() => chart?.resize())
      observer.observe(el)
    }
    if (option.value) chart.setOption(option.value, true)
  }

  watch([container, option], () => render(), { deep: true, flush: 'post' })

  onMounted(() => render())

  onBeforeUnmount(() => dispose())
}

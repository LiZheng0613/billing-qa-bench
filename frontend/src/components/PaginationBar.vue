<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    total: number
    page: number
    pageSize: number
    pageSizeOptions?: number[]
    loading?: boolean
    /** 统计单位，如「条用例」「条任务」「次执行」。 */
    unit?: string
    showSummary?: boolean
    showPageSize?: boolean
    showJump?: boolean
  }>(),
  {
    pageSizeOptions: () => [20, 50, 100],
    loading: false,
    unit: '条',
    showSummary: true,
    showPageSize: true,
    showJump: true,
  },
)

const emit = defineEmits<{
  'update:page': [value: number]
  'update:pageSize': [value: number]
}>()

const pageCount = computed(() => Math.max(1, Math.ceil(props.total / Math.max(1, props.pageSize))))
const formattedTotal = computed(() => props.total.toLocaleString('zh-CN'))

/**
 * 页码滑窗：总页数 ≤ 7 全展开；否则首末页常驻、当前页左右各 1，
 * 中间用省略号占位，固定 7 个槽位避免翻页时布局跳动。
 */
const pageItems = computed<Array<number | 'gap'>>(() => {
  const last = pageCount.value
  const current = Math.min(Math.max(1, props.page), last)
  if (last <= 7) return Array.from({ length: last }, (_, index) => index + 1)
  if (current <= 4) return [1, 2, 3, 4, 5, 'gap', last]
  if (current >= last - 3) return [1, 'gap', last - 4, last - 3, last - 2, last - 1, last]
  return [1, 'gap', current - 1, current, current + 1, 'gap', last]
})

function go(next: number): void {
  if (props.loading) return
  if (next < 1 || next > pageCount.value || next === props.page) return
  emit('update:page', next)
}

const jumpValue = ref(String(props.page))

watch(
  () => [props.page, pageCount.value] as const,
  () => {
    jumpValue.value = String(Math.min(Math.max(1, props.page), pageCount.value))
  },
)

function onJumpInput(event: Event): void {
  const input = event.target as HTMLInputElement
  const maxDigits = String(pageCount.value).length
  const digits = input.value.replace(/\D/g, '').slice(0, maxDigits)
  // 直接回写 DOM，保证输入非法字符时视图立即被纠正
  input.value = digits
  jumpValue.value = digits
}

function commitJump(): void {
  if (props.loading) return
  const digits = jumpValue.value.replace(/\D/g, '')
  if (!digits) {
    jumpValue.value = String(props.page)
    return
  }
  const next = Math.min(pageCount.value, Math.max(1, Number(digits)))
  jumpValue.value = String(next)
  go(next)
}

function resetJump(): void {
  jumpValue.value = String(props.page)
}

function onPageSizeChange(event: Event): void {
  const select = event.target as HTMLSelectElement
  const size = Number(select.value)
  if (!Number.isFinite(size) || size <= 0 || size === props.pageSize) return
  emit('update:pageSize', size)
}
</script>

<template>
  <nav
    class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 border-t border-line bg-white px-4 py-2.5"
    :class="{ 'opacity-70': loading }"
    aria-label="分页"
    :aria-busy="loading || undefined"
  >
    <div class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-500">
      <span v-if="showSummary" class="whitespace-nowrap">
        共 <b class="font-medium tabular-nums text-slate-700">{{ formattedTotal }}</b> {{ unit }}
      </span>
      <template v-if="showPageSize">
        <span v-if="showSummary" class="text-slate-300" aria-hidden="true">·</span>
        <label class="hidden items-center gap-1.5 sm:flex">
          <span class="whitespace-nowrap">每页</span>
          <!-- 复用项目统一下拉样式（select.input）：同一套箭头、展开面板与选中态，仅收窄尺寸适配分页条 -->
          <select
            :value="pageSize"
            class="input !h-7 !w-[74px] !py-0 !pl-2.5 !pr-7 text-[11px]"
            :disabled="loading"
            aria-label="每页条数"
            @change="onPageSizeChange"
          >
            <option v-for="size in pageSizeOptions" :key="size" :value="size">
              {{ size }} 条
            </option>
          </select>
        </label>
      </template>
      <slot name="extra" />
      <span v-if="loading" class="inline-flex items-center gap-1 text-slate-400">
        <span
          class="h-3 w-3 animate-spin rounded-full border-2 border-brand-edge border-t-brand"
          aria-hidden="true"
        />
        加载中
      </span>
    </div>

    <div class="flex flex-none items-center gap-0.5">
      <!-- 窄屏：前后翻页 + 第 X / Y 页 -->
      <div class="flex items-center gap-0.5 md:hidden">
        <button
          type="button"
          class="pager-arrow"
          :disabled="loading || page <= 1"
          aria-label="上一页"
          @click="go(page - 1)"
        >
          ‹
        </button>
        <span class="whitespace-nowrap px-1 text-[11px] tabular-nums text-slate-500">
          第 <b class="font-medium text-slate-700">{{ page }}</b> / {{ pageCount }} 页
        </span>
        <button
          type="button"
          class="pager-arrow"
          :disabled="loading || page >= pageCount"
          aria-label="下一页"
          @click="go(page + 1)"
        >
          ›
        </button>
      </div>

      <!-- 桌面：上一页 / 页码滑窗 / 下一页 / 跳转 -->
      <div class="hidden items-center gap-0.5 md:flex">
        <button
          type="button"
          class="pager-arrow"
          :disabled="loading || page <= 1"
          @click="go(page - 1)"
        >
          ‹ 上一页
        </button>
        <template v-for="(item, index) in pageItems" :key="`${item}-${index}`">
          <span
            v-if="item === 'gap'"
            class="w-6 select-none text-center text-xs text-slate-400"
            aria-hidden="true"
          >
            …
          </span>
          <button
            v-else
            type="button"
            class="pager-page"
            :class="{ 'pager-page-active': item === page }"
            :aria-current="item === page ? 'page' : undefined"
            :aria-label="`第 ${item} 页`"
            :disabled="loading"
            @click="go(item)"
          >
            {{ item }}
          </button>
        </template>
        <button
          type="button"
          class="pager-arrow"
          :disabled="loading || page >= pageCount"
          @click="go(page + 1)"
        >
          下一页 ›
        </button>

        <template v-if="showJump">
          <span class="mx-1 h-4 w-px bg-line" aria-hidden="true" />
          <label class="flex items-center gap-1.5 whitespace-nowrap text-[11px] text-slate-500">
            跳至
            <input
              :value="jumpValue"
              type="text"
              inputmode="numeric"
              autocomplete="off"
              class="h-7 w-[52px] rounded-lg border border-line bg-white text-center text-xs font-medium tabular-nums text-slate-700 outline-none transition placeholder:text-slate-300 hover:border-slate-300 focus:border-brand focus:ring-2 focus:ring-brand/15 disabled:cursor-not-allowed"
              :disabled="loading"
              aria-label="跳转页码"
              @input="onJumpInput"
              @keydown.enter.prevent="commitJump"
              @keydown.esc.prevent="resetJump"
              @blur="commitJump"
            />
            页
          </label>
        </template>
      </div>
    </div>
  </nav>
</template>

<style scoped>
.pager-arrow {
  display: inline-flex;
  height: 1.75rem;
  align-items: center;
  justify-content: center;
  gap: 0.25rem;
  border: 1px solid transparent;
  border-radius: 0.5rem;
  background: #fff;
  padding: 0 0.5rem;
  font-size: 0.75rem;
  font-weight: 500;
  color: #475569;
  white-space: nowrap;
  transition:
    background-color 150ms,
    color 150ms;
}

.pager-arrow:hover:not(:disabled) {
  background: #f1f5f9;
  color: #1e293b;
}

.pager-arrow:disabled {
  cursor: not-allowed;
  color: #cbd5e1;
}

.pager-page {
  display: inline-flex;
  height: 1.75rem;
  min-width: 1.75rem;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: 0.5rem;
  background: #fff;
  padding: 0 0.25rem;
  font-size: 0.75rem;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  color: #475569;
  transition:
    background-color 150ms,
    color 150ms,
    border-color 150ms;
}

.pager-page:hover:not(:disabled) {
  background: #f1f5f9;
  color: #1e293b;
}

.pager-page:disabled {
  cursor: not-allowed;
  color: #cbd5e1;
}

.pager-page-active,
.pager-page-active:hover:not(:disabled),
.pager-page-active:disabled {
  border-color: theme('colors.brand.DEFAULT');
  background: theme('colors.brand.DEFAULT');
  color: #fff;
  box-shadow: 0 6px 14px -8px rgb(79 70 229 / 0.95);
  cursor: default;
}
</style>

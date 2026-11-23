<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { api } from '../../api'
import BaseModal from '../../components/BaseModal.vue'
import SelectButton from '../../components/SelectButton.vue'
import type { BatchTask } from '../../types'
import { errorMessage } from '../../utils/errors'
import { useToastStore } from '../../stores/toast'

const props = defineProps<{
  modelValue: boolean
  /** 计划根或任意子批次：后端自动解析到所属计划。 */
  task: BatchTask | null
}>()
const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()

const toast = useToastStore()

const form = reactive({
  format: 'xlsx' as 'xlsx' | 'jsonl',
  round_scope: 'latest' as 'latest' | 'all',
  include_raw: false,
  include_attempts: false,
})
const exporting = ref(false)

const subtitle = computed(() =>
  props.task
    ? `聚合「${props.task.name}」所属计划的各轮次执行：轮次对比、用例稳定性、跨轮问题清单与多轮明细`
    : '聚合计划各轮次执行：轮次对比、用例稳定性、跨轮问题清单与多轮明细',
)

function resetForm(): void {
  form.format = 'xlsx'
  form.round_scope = 'latest'
  form.include_raw = false
  form.include_attempts = false
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) resetForm()
  },
)

async function exportRounds(): Promise<void> {
  const task = props.task
  if (!task || exporting.value) return
  exporting.value = true
  try {
    const stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '')
    await api.tasks.exportRounds(task.id, `${task.code}_rounds_${stamp}.${form.format}`, {
      format: form.format,
      round_scope: form.round_scope,
      include_raw: form.include_raw,
      include_attempts: form.include_attempts,
    })
    toast.success(
      form.format === 'jsonl'
        ? '已导出多轮完整数据（JSONL，无长度截断）'
        : '已导出计划多轮报表',
    )
    emit('update:modelValue', false)
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <BaseModal
    :model-value="modelValue"
    title="导出多轮报表"
    :subtitle="subtitle"
    width="md"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="space-y-4">
      <div class="grid gap-3 sm:grid-cols-2">
        <div>
          <label class="label">导出格式</label>
          <select v-model="form.format" class="input">
            <SelectButton />
            <option value="xlsx">Excel 报表（人读决策视图）</option>
            <option value="jsonl">JSONL（完整数据，程序消费）</option>
          </select>
          <p class="mt-1 text-[11px] text-slate-500">
            Excel 含综合概览 / 轮次对比 / 跨轮问题清单 / 用例稳定性 / 多轮用例明细 / 口径说明；JSONL 不截断任何字段。
          </p>
        </div>
        <div>
          <label class="label">轮次范围</label>
          <select v-model="form.round_scope" class="input">
            <SelectButton />
            <option value="latest">最近一次轮次链（默认）</option>
            <option value="all">全部历次执行（按轮次链分组）</option>
          </select>
          <p class="mt-1 text-[11px] text-slate-500">
            周期计划多次触发会形成多条轮次链；默认只导出最近一次触发的完整轮次链。
          </p>
        </div>
      </div>

      <div class="space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-3">
        <label class="flex items-start gap-2 text-sm text-slate-700">
          <input
            v-model="form.include_attempts"
            type="checkbox"
            class="mt-0.5 h-4 w-4 rounded border-slate-300 bg-white"
          />
          <span>
            展开失败重试的每次尝试
            <span class="block text-[11px] text-slate-500">
              默认每轮每用例只取最新一次尝试参与统计；勾选后在明细中保留全部尝试记录。
            </span>
          </span>
        </label>
        <label
          class="flex items-start gap-2 text-sm"
          :class="form.format === 'xlsx' ? 'text-slate-700' : 'text-slate-400'"
        >
          <input
            v-model="form.include_raw"
            type="checkbox"
            class="mt-0.5 h-4 w-4 rounded border-slate-300 bg-white"
            :disabled="form.format !== 'xlsx'"
          />
          <span>
            附加「原始明细」页（仅 Excel）
            <span class="block text-[11px] text-slate-500">
              完整字段 + 轮次轨迹 JSON，文件会显著变大；超长字段按 Excel 上限截断，需要完整内容请用 JSONL。
            </span>
          </span>
        </label>
      </div>
    </div>

    <template #footer>
      <button class="btn-ghost" :disabled="exporting" @click="emit('update:modelValue', false)">
        取消
      </button>
      <button class="btn-primary" :disabled="exporting || !task" @click="exportRounds">
        {{ exporting ? '导出中…' : '导出' }}
      </button>
    </template>
  </BaseModal>
</template>

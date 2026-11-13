<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { api } from '../../api'
import type {
  CasePayload,
  CaseType,
  Environment,
  HiddenContextItem,
  JudgeStrategy,
  ModelConfig,
  PromptListItem,
  RuleConfig,
  TemplateStrategy,
  TestCase,
  TurnItem,
} from '../../types'
import { caseTypeLabel } from '../../utils/format'
import { useToastStore } from '../../stores/toast'

interface AmountRule {
  label: string
  value: number | null
  tolerance: number
}

interface HiddenRule {
  key: string
  content: string
  release_hint: string
}

interface TurnRule {
  query: string
  expected_answer: string
  notes: string
  rulesJson: string
}

const props = defineProps<{
  modelValue: boolean
  caseData: TestCase | null
  environments: Environment[]
  templates: PromptListItem[]
  models: ModelConfig[]
  saving: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'submit', payload: CasePayload): void
}>()

const toast = useToastStore()

const CASE_TYPES: CaseType[] = ['SINGLE', 'STATIC_MULTI', 'DYNAMIC_MULTI']
const simulatorVersions = ref<number[]>([])
const judgeVersions = ref<number[]>([])
const sessionExpectedText = ref('')

const form = reactive({
  code: '',
  name: '',
  description: '',
  case_type: 'SINGLE' as CaseType,
  status: 'ACTIVE',
  priority: 'P1',
  tagsText: '',
  environment_id: null as number | null,
  query: '',
  expected_answer: '',
  amounts: [] as AmountRule[],
  counts: [] as AmountRule[],
  datesText: '',
  mustContainText: '',
  mustNotContainText: '',
  turns: [] as TurnRule[],
  failFast: false,
  userGoal: '',
  initialQuery: '',
  persona: '',
  maxTurns: 5,
  hidden: [] as HiddenRule[],
  terminateKeywordsText: '',
  simulator_template_id: null as number | null,
  simulatorStrategy: 'LATEST' as TemplateStrategy,
  simulatorVersion: null as number | null,
  judge_template_id: null as number | null,
  judgeTemplateStrategy: 'LATEST' as TemplateStrategy,
  judgeVersion: null as number | null,
  simulator_model_id: null as number | null,
  judge_model_ids: [] as number[],
  judgeStrategy: 'AVG' as JudgeStrategy,
  pass_threshold: 60,
  change_summary: '',
})

const isEdit = computed(() => Boolean(props.caseData))
const title = computed(() => (isEdit.value ? `编辑用例 · v${props.caseData?.current_version}` : '新建用例'))

const simulatorTemplates = computed(() =>
  props.templates.filter((item) => item.template_type === 'USER_SIMULATOR'),
)
const judgeTemplates = computed(() =>
  props.templates.filter((item) => item.template_type === 'LLM_JUDGE'),
)
const simulatorModels = computed(() => props.models.filter((item) => item.role === 'SIMULATOR'))
const judgeModels = computed(() => props.models.filter((item) => item.role === 'JUDGE'))

function splitList(text: string): string[] {
  return text
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function rulesFromCase(rules: RuleConfig | null | undefined): void {
  form.amounts = (rules?.amounts ?? []).map((item) => ({
    label: item.label,
    value: item.value,
    tolerance: item.tolerance ?? 0,
  }))
  form.counts = (rules?.counts ?? []).map((item) => ({
    label: item.label,
    value: item.value,
    tolerance: item.tolerance ?? 0,
  }))
  form.datesText = (rules?.dates ?? []).join(', ')
  form.mustContainText = (rules?.must_contain ?? []).join(', ')
  form.mustNotContainText = (rules?.must_not_contain ?? []).join(', ')
}

function buildRules(): RuleConfig | null {
  const amounts = form.amounts
    .filter((item) => item.label.trim() && item.value !== null && Number.isFinite(item.value))
    .map((item) => ({
      label: item.label.trim(),
      value: Number(item.value),
      tolerance: Number(item.tolerance) || 0,
    }))
  const counts = form.counts
    .filter((item) => item.label.trim() && item.value !== null && Number.isFinite(item.value))
    .map((item) => ({
      label: item.label.trim(),
      value: Number(item.value),
      tolerance: Number(item.tolerance) || 0,
    }))
  const dates = splitList(form.datesText)
  const must = splitList(form.mustContainText)
  const mustNot = splitList(form.mustNotContainText)
  if (!amounts.length && !counts.length && !dates.length && !must.length && !mustNot.length) {
    return null
  }
  return {
    amounts,
    counts,
    dates,
    must_contain: must,
    must_not_contain: mustNot,
  }
}

function resetForm(): void {
  const source = props.caseData
  form.code = source?.code ?? ''
  form.name = source?.name ?? ''
  form.description = source?.description ?? ''
  form.case_type = (source?.case_type as CaseType) ?? 'SINGLE'
  form.status = source?.status ?? 'ACTIVE'
  form.priority = source?.priority ?? 'P1'
  form.tagsText = (source?.tags ?? []).join(', ')
  form.environment_id = source?.environment_id ?? props.environments[0]?.id ?? null
  form.query = source?.query ?? ''
  form.expected_answer = source?.expected_answer ?? ''
  rulesFromCase(source?.expected_rules)
  form.turns =
    source?.turns && source.turns.length
      ? source.turns.map((turn) => ({
          query: turn.query,
          expected_answer: turn.expected_answer ?? '',
          notes: turn.notes ?? '',
          rulesJson: turn.expected_rules ? JSON.stringify(turn.expected_rules, null, 2) : '',
        }))
      : [{ query: '', expected_answer: '', notes: '', rulesJson: '' }]
  sessionExpectedText.value = source?.session_expected
    ? JSON.stringify(source.session_expected, null, 2)
    : ''
  form.failFast = source?.fail_fast ?? false
  form.userGoal = source?.user_goal ?? ''
  form.initialQuery = source?.initial_query ?? ''
  form.persona = source?.persona_description ?? ''
  form.maxTurns = source?.max_turns ?? 5
  form.hidden = (source?.hidden_context ?? []).map((item) => ({
    key: item.key,
    content: item.content,
    release_hint: item.release_hint ?? '',
  }))
  if (form.hidden.length === 0) form.hidden = [{ key: '', content: '', release_hint: '' }]
  const keywords = (source?.terminate_conditions?.keywords ?? []) as unknown
  form.terminateKeywordsText = Array.isArray(keywords) ? keywords.map(String).join(', ') : ''
  form.simulator_template_id =
    source?.simulator_template_id ?? simulatorTemplates.value[0]?.id ?? null
  form.simulatorStrategy = (source?.simulator_template_strategy as TemplateStrategy) ?? 'LATEST'
  form.simulatorVersion = source?.simulator_template_version ?? null
  form.judge_template_id = source?.judge_template_id ?? judgeTemplates.value[0]?.id ?? null
  form.judgeTemplateStrategy = (source?.judge_template_strategy as TemplateStrategy) ?? 'LATEST'
  form.judgeVersion = source?.judge_template_version ?? null
  form.simulator_model_id = source?.simulator_model_id ?? simulatorModels.value[0]?.id ?? null
  form.judge_model_ids = source?.judge_model_ids ?? judgeModels.value.map((item) => item.id)
  form.judgeStrategy = (source?.judge_strategy as JudgeStrategy) ?? 'AVG'
  form.pass_threshold = source?.pass_threshold ?? 60
  form.change_summary = ''
  void loadVersions('simulator')
  void loadVersions('judge')
}

async function loadVersions(kind: 'simulator' | 'judge'): Promise<void> {
  const templateId = kind === 'simulator' ? form.simulator_template_id : form.judge_template_id
  const target = kind === 'simulator' ? simulatorVersions : judgeVersions
  if (!templateId) {
    target.value = []
    return
  }
  try {
    const detail = await api.prompts.get(templateId)
    target.value = detail.versions.map((item) => item.version).sort((a, b) => b - a)
    if (kind === 'simulator' && form.simulatorVersion === null) {
      form.simulatorVersion = target.value[0] ?? null
    }
    if (kind === 'judge' && form.judgeVersion === null) {
      form.judgeVersion = target.value[0] ?? null
    }
  } catch {
    target.value = []
  }
}

watch(
  () => [props.modelValue, props.caseData] as const,
  ([open]) => {
    if (open) {
      resetForm()
      document.body.style.overflow = 'hidden'
    } else {
      document.body.style.overflow = ''
    }
  },
  { immediate: true },
)

watch(
  () => form.simulator_template_id,
  () => {
    if (props.modelValue) void loadVersions('simulator')
  },
)
watch(
  () => form.judge_template_id,
  () => {
    if (props.modelValue) void loadVersions('judge')
  },
)

function close(): void {
  emit('update:modelValue', false)
}

function addAmount(): void {
  form.amounts.push({ label: '金额', value: null, tolerance: 0.01 })
}

function addCount(): void {
  form.counts.push({ label: '笔数', value: null, tolerance: 0 })
}

function addTurn(): void {
  form.turns.push({ query: '', expected_answer: '', notes: '', rulesJson: '' })
}

function addHidden(): void {
  form.hidden.push({ key: '', content: '', release_hint: '' })
}

function toggleJudge(modelId: number): void {
  const index = form.judge_model_ids.indexOf(modelId)
  if (index >= 0) form.judge_model_ids.splice(index, 1)
  else form.judge_model_ids.push(modelId)
}

function parseTurnRules(text: string): RuleConfig | null {
  const trimmed = text.trim()
  if (!trimmed) return null
  const parsed = JSON.parse(trimmed) as RuleConfig
  return {
    amounts: parsed.amounts ?? [],
    counts: parsed.counts ?? [],
    dates: parsed.dates ?? [],
    must_contain: parsed.must_contain ?? [],
    must_not_contain: parsed.must_not_contain ?? [],
  }
}

function buildPayload(): CasePayload | null {
  if (!form.code.trim()) {
    toast.error('请填写用例编号')
    return null
  }
  if (!form.name.trim()) {
    toast.error('请填写用例名称')
    return null
  }
  if (form.case_type === 'SINGLE' && !form.query.trim()) {
    toast.error('单轮用例必须填写用户 query')
    return null
  }
  if (form.case_type === 'STATIC_MULTI' && form.turns.every((turn) => !turn.query.trim())) {
    toast.error('静态多轮至少需要一轮 query')
    return null
  }
  if (form.case_type === 'DYNAMIC_MULTI' && !form.userGoal.trim()) {
    toast.error('动态多轮必须填写用户目标')
    return null
  }

  const payload: CasePayload = {
    code: form.code.trim(),
    name: form.name.trim(),
    description: form.description.trim() || null,
    case_type: form.case_type,
    status: form.status,
    priority: form.priority,
    tags: splitList(form.tagsText),
    environment_id: form.environment_id,
    max_turns: form.maxTurns,
    simulator_template_id: form.simulator_template_id,
    simulator_template_strategy: form.simulatorStrategy,
    simulator_template_version:
      form.simulatorStrategy === 'PINNED' ? form.simulatorVersion : null,
    judge_template_id: form.judge_template_id,
    judge_template_strategy: form.judgeTemplateStrategy,
    judge_template_version: form.judgeTemplateStrategy === 'PINNED' ? form.judgeVersion : null,
    simulator_model_id: form.simulator_model_id,
    judge_model_ids: [...form.judge_model_ids],
    judge_strategy: form.judgeStrategy,
    pass_threshold: form.pass_threshold,
    prompt_overrides: props.caseData?.prompt_overrides ?? null,
    change_summary: form.change_summary.trim() || null,
  }

  if (form.case_type === 'SINGLE') {
    payload.query = form.query.trim()
    payload.expected_answer = form.expected_answer.trim() || null
    payload.expected_rules = buildRules()
    payload.turns = null
    payload.fail_fast = false
  } else if (form.case_type === 'STATIC_MULTI') {
    const turns: TurnItem[] = []
    for (let index = 0; index < form.turns.length; index += 1) {
      const turn = form.turns[index]
      if (!turn.query.trim()) continue
      try {
        turns.push({
          index: turns.length + 1,
          query: turn.query.trim(),
          expected_answer: turn.expected_answer.trim() || null,
          notes: turn.notes.trim() || null,
          expected_rules: parseTurnRules(turn.rulesJson),
        })
      } catch {
        toast.error(`第 ${index + 1} 轮的预期规则 JSON 格式不正确`)
        return null
      }
    }
    payload.turns = turns
    payload.fail_fast = form.failFast
    payload.query = null
    payload.expected_answer = null
    payload.expected_rules = null
    try {
      payload.session_expected = sessionExpectedText.value.trim()
        ? (JSON.parse(sessionExpectedText.value) as Record<string, unknown>)
        : null
    } catch {
      toast.error('会话级预期 JSON 格式不正确')
      return null
    }
  } else {
    const hidden: HiddenContextItem[] = form.hidden
      .filter((item) => item.key.trim() && item.content.trim())
      .map((item) => ({
        key: item.key.trim(),
        content: item.content.trim(),
        release_hint: item.release_hint.trim() || null,
      }))
    const keywords = splitList(form.terminateKeywordsText)
    payload.user_goal = form.userGoal.trim()
    payload.initial_query = form.initialQuery.trim() || null
    payload.persona_description = form.persona.trim() || null
    payload.hidden_context = hidden
    payload.terminate_conditions = keywords.length ? { keywords } : null
    payload.expected_answer = form.expected_answer.trim() || null
    payload.expected_rules = buildRules()
    payload.turns = null
    payload.query = null
  }

  return payload
}

function submit(): void {
  const payload = buildPayload()
  if (payload) emit('submit', payload)
}
</script>

<template>
  <Teleport to="body">
    <Transition name="drawer">
      <div
        v-if="modelValue"
        class="fixed inset-0 z-50 bg-slate-950/70 backdrop-blur-sm"
        @click.self="close"
      >
        <aside
          class="absolute right-0 top-0 flex h-full w-full max-w-3xl flex-col border-l border-slate-800 bg-slate-900 shadow-2xl"
        >
          <header class="flex items-start justify-between gap-3 border-b border-slate-800 px-5 py-4">
            <div class="min-w-0">
              <h2 class="text-base font-semibold text-slate-100">{{ title }}</h2>
              <p class="mt-1 text-xs text-slate-500">
                保存后自动生成版本快照，可用于 Diff 对比与一键回滚
              </p>
            </div>
            <button
              class="rounded-lg p-1.5 text-slate-500 transition hover:bg-slate-800 hover:text-slate-200"
              @click="close"
            >
              <svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" d="M6 6l12 12M18 6 6 18" />
              </svg>
            </button>
          </header>

          <div class="flex-1 space-y-6 overflow-y-auto px-5 py-5">
            <section class="space-y-3">
              <h3 class="text-xs font-semibold uppercase tracking-wider text-indigo-300">基础信息</h3>
              <div class="grid gap-3 sm:grid-cols-2">
                <div>
                  <label class="label">用例编号</label>
                  <input v-model="form.code" class="input font-mono" :disabled="isEdit" placeholder="CASE-BILL-001" />
                </div>
                <div>
                  <label class="label">用例名称</label>
                  <input v-model="form.name" class="input" placeholder="账单金额与明细一致性校验" />
                </div>
              </div>
              <div>
                <label class="label">描述</label>
                <textarea v-model="form.description" class="input" rows="2" placeholder="用例背景与关注点" />
              </div>
              <div class="grid gap-3 sm:grid-cols-3">
                <div>
                  <label class="label">状态</label>
                  <select v-model="form.status" class="input">
                    <option value="ACTIVE">启用</option>
                    <option value="DRAFT">草稿</option>
                    <option value="DISABLED">停用</option>
                  </select>
                </div>
                <div>
                  <label class="label">优先级</label>
                  <select v-model="form.priority" class="input">
                    <option value="P0">P0</option>
                    <option value="P1">P1</option>
                    <option value="P2">P2</option>
                    <option value="P3">P3</option>
                  </select>
                </div>
                <div>
                  <label class="label">标签（逗号分隔）</label>
                  <input v-model="form.tagsText" class="input" placeholder="账单核验, 金额" />
                </div>
              </div>
              <div>
                <label class="label">执行环境</label>
                <select v-model="form.environment_id" class="input">
                  <option :value="null">未绑定</option>
                  <option v-for="env in environments" :key="env.id" :value="env.id">
                    {{ env.name }}（{{ env.code }}）
                  </option>
                </select>
              </div>
            </section>

            <section class="space-y-3">
              <h3 class="text-xs font-semibold uppercase tracking-wider text-indigo-300">用例类型</h3>
              <div class="flex flex-wrap gap-2">
                <button
                  v-for="type in CASE_TYPES"
                  :key="type"
                  class="rounded-lg border px-3 py-1.5 text-xs font-medium transition"
                  :class="
                    form.case_type === type
                      ? 'border-indigo-500/60 bg-indigo-500/15 text-indigo-200'
                      : 'border-slate-700 text-slate-400 hover:bg-slate-800'
                  "
                  :disabled="isEdit"
                  @click="form.case_type = type"
                >
                  {{ caseTypeLabel(type) }}
                </button>
              </div>
              <p class="text-[11px] text-slate-500">
                用例类型创建后不可修改，如需切换请新建用例。
              </p>
            </section>

            <section v-if="form.case_type === 'SINGLE'" class="space-y-3">
              <h3 class="text-xs font-semibold uppercase tracking-wider text-indigo-300">单轮配置</h3>
              <div>
                <label class="label">用户提问（query）</label>
                <textarea v-model="form.query" class="input" rows="2" placeholder="帮我查一下订单 A2024001 的含税金额和明细笔数" />
              </div>
              <div>
                <label class="label">预期答案</label>
                <textarea v-model="form.expected_answer" class="input" rows="2" placeholder="订单 A2024001 含税金额为 ¥12,345.67，共 3 笔明细" />
              </div>
            </section>

            <section v-else-if="form.case_type === 'STATIC_MULTI'" class="space-y-3">
              <div class="flex items-center justify-between">
                <h3 class="text-xs font-semibold uppercase tracking-wider text-indigo-300">静态多轮脚本</h3>
                <button class="btn-ghost btn-xs" @click="addTurn">添加一轮</button>
              </div>
              <div
                v-for="(turn, index) in form.turns"
                :key="index"
                class="rounded-xl border border-slate-800 bg-slate-950/50 p-3"
              >
                <div class="flex items-center justify-between">
                  <span class="text-xs font-medium text-slate-400">第 {{ index + 1 }} 轮</span>
                  <button
                    v-if="form.turns.length > 1"
                    class="text-[11px] text-rose-400 hover:text-rose-300"
                    @click="form.turns.splice(index, 1)"
                  >
                    删除
                  </button>
                </div>
                <div class="mt-2 space-y-2">
                  <input v-model="turn.query" class="input" placeholder="本轮用户提问，例如：那含税总额是多少？" />
                  <input v-model="turn.expected_answer" class="input" placeholder="本轮预期答案（可选）" />
                  <input v-model="turn.notes" class="input" placeholder="备注（可选）" />
                  <textarea
                    v-model="turn.rulesJson"
                    class="input font-mono text-xs"
                    rows="2"
                    placeholder='本轮规则 JSON（可选）：{"amounts":[{"label":"金额","value":12345.67}]}'
                  />
                </div>
              </div>
              <div>
                <label class="label">会话级预期 JSON（可选）</label>
                <textarea
                  v-model="sessionExpectedText"
                  class="input font-mono text-xs"
                  rows="3"
                  placeholder='{"expected_answer":"...","expected_rules":{"amounts":[...]}}'
                />
              </div>
              <label class="flex items-center gap-2 text-sm text-slate-300">
                <input v-model="form.failFast" type="checkbox" class="h-4 w-4 rounded border-slate-600 bg-slate-900" />
                任一规则失败即中止后续轮次（fail_fast）
              </label>
            </section>

            <section v-else class="space-y-3">
              <h3 class="text-xs font-semibold uppercase tracking-wider text-indigo-300">动态多轮配置</h3>
              <div>
                <label class="label">用户目标（user_goal）</label>
                <textarea v-model="form.userGoal" class="input" rows="2" placeholder="在不直接提供订单号的前提下，通过追问拿到订单 A2024001 的含税金额与明细笔数" />
              </div>
              <div>
                <label class="label">初始提问（initial_query）</label>
                <textarea v-model="form.initialQuery" class="input" rows="2" placeholder="我想核对一下最近订单的账单明细" />
              </div>
              <div>
                <label class="label">人设描述（persona_description）</label>
                <textarea v-model="form.persona" class="input" rows="2" placeholder="某科技公司财务，表达口语化，关注金额细节" />
              </div>
              <div class="grid gap-3 sm:grid-cols-2">
                <div>
                  <label class="label">最大轮次</label>
                  <input v-model.number="form.maxTurns" class="input" type="number" min="1" max="20" />
                </div>
                <div>
                  <label class="label">终止关键词（逗号分隔）</label>
                  <input v-model="form.terminateKeywordsText" class="input" placeholder="¥12,345.67, 3 笔" />
                </div>
              </div>
              <div class="flex items-center justify-between">
                <label class="label !mb-0">隐藏上下文（按提示逐轮释放）</label>
                <button class="btn-ghost btn-xs" @click="addHidden">添加条目</button>
              </div>
              <div
                v-for="(item, index) in form.hidden"
                :key="index"
                class="grid gap-2 rounded-xl border border-slate-800 bg-slate-950/50 p-3 sm:grid-cols-[120px_1fr_110px_auto]"
              >
                <input v-model="item.key" class="input" placeholder="键，如 order_no" />
                <input v-model="item.content" class="input" placeholder="内容，如 A2024001" />
                <input v-model="item.release_hint" class="input" placeholder="释放提示" />
                <button class="btn-danger btn-xs" @click="form.hidden.splice(index, 1)">删除</button>
              </div>
            </section>

            <section v-if="form.case_type !== 'STATIC_MULTI'" class="space-y-3">
              <div class="flex items-center justify-between">
                <h3 class="text-xs font-semibold uppercase tracking-wider text-indigo-300">规则硬校验</h3>
                <div class="flex gap-2">
                  <button class="btn-ghost btn-xs" @click="addAmount">添加金额</button>
                  <button class="btn-ghost btn-xs" @click="addCount">添加笔数</button>
                </div>
              </div>
              <div v-for="(rule, index) in form.amounts" :key="`amount-${index}`" class="flex gap-2">
                <input v-model="rule.label" class="input" placeholder="标签" />
                <input v-model.number="rule.value" class="input" type="number" step="0.01" placeholder="期望金额" />
                <input v-model.number="rule.tolerance" class="input !w-28" type="number" step="0.01" placeholder="容差" />
                <button class="btn-danger btn-xs shrink-0" @click="form.amounts.splice(index, 1)">删除</button>
              </div>
              <div v-for="(rule, index) in form.counts" :key="`count-${index}`" class="flex gap-2">
                <input v-model="rule.label" class="input" placeholder="标签" />
                <input v-model.number="rule.value" class="input" type="number" placeholder="期望笔数" />
                <button class="btn-danger btn-xs shrink-0" @click="form.counts.splice(index, 1)">删除</button>
              </div>
              <div class="grid gap-3 sm:grid-cols-3">
                <div>
                  <label class="label">日期（逗号分隔）</label>
                  <input v-model="form.datesText" class="input" placeholder="2026-10-01" />
                </div>
                <div>
                  <label class="label">必须包含</label>
                  <input v-model="form.mustContainText" class="input" placeholder="A2024001" />
                </div>
                <div>
                  <label class="label">禁止包含</label>
                  <input v-model="form.mustNotContainText" class="input" placeholder="系统异常" />
                </div>
              </div>
            </section>

            <section class="space-y-3">
              <h3 class="text-xs font-semibold uppercase tracking-wider text-indigo-300">Prompt 与模型</h3>
              <div class="rounded-xl border border-slate-800 bg-slate-950/40 p-3">
                <label class="label">用户模拟器模板（USER_SIMULATOR）</label>
                <div class="grid gap-2 sm:grid-cols-[minmax(0,1fr)_130px_130px]">
                  <select v-model="form.simulator_template_id" class="input">
                    <option :value="null">未指定</option>
                    <option v-for="item in simulatorTemplates" :key="item.id" :value="item.id">
                      {{ item.code }} · v{{ item.current_version }}
                    </option>
                  </select>
                  <select v-model="form.simulatorStrategy" class="input">
                    <option value="LATEST">跟随最新</option>
                    <option value="PINNED">锁定版本</option>
                  </select>
                  <select
                    v-if="form.simulatorStrategy === 'PINNED'"
                    v-model="form.simulatorVersion"
                    class="input"
                  >
                    <option :value="null">选择版本</option>
                    <option v-for="version in simulatorVersions" :key="version" :value="version">
                      v{{ version }}
                    </option>
                  </select>
                </div>
              </div>

              <div class="rounded-xl border border-slate-800 bg-slate-950/40 p-3">
                <label class="label">LLM 裁判模板（LLM_JUDGE）</label>
                <div class="grid gap-2 sm:grid-cols-[minmax(0,1fr)_130px_130px]">
                  <select v-model="form.judge_template_id" class="input">
                    <option :value="null">未指定</option>
                    <option v-for="item in judgeTemplates" :key="item.id" :value="item.id">
                      {{ item.code }} · v{{ item.current_version }}
                    </option>
                  </select>
                  <select v-model="form.judgeTemplateStrategy" class="input">
                    <option value="LATEST">跟随最新</option>
                    <option value="PINNED">锁定版本</option>
                  </select>
                  <select v-if="form.judgeTemplateStrategy === 'PINNED'" v-model="form.judgeVersion" class="input">
                    <option :value="null">选择版本</option>
                    <option v-for="version in judgeVersions" :key="version" :value="version">
                      v{{ version }}
                    </option>
                  </select>
                </div>
              </div>

              <div class="grid gap-3 sm:grid-cols-2">
                <div>
                  <label class="label">模拟器模型</label>
                  <select v-model="form.simulator_model_id" class="input">
                    <option :value="null">未指定</option>
                    <option v-for="item in simulatorModels" :key="item.id" :value="item.id">
                      {{ item.name }}（{{ item.model_name }}）
                    </option>
                  </select>
                </div>
                <div>
                  <label class="label">裁判策略</label>
                  <select v-model="form.judgeStrategy" class="input">
                    <option value="AVG">平均分（AVG）</option>
                    <option value="VETO">一票否决（VETO）</option>
                  </select>
                </div>
              </div>

              <div>
                <label class="label">裁判模型（可多选）</label>
                <div class="flex flex-wrap gap-2">
                  <label
                    v-for="item in judgeModels"
                    :key="item.id"
                    class="flex cursor-pointer items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs transition"
                    :class="
                      form.judge_model_ids.includes(item.id)
                        ? 'border-indigo-500/50 bg-indigo-500/10 text-indigo-200'
                        : 'border-slate-700 text-slate-400 hover:bg-slate-800'
                    "
                  >
                    <input
                      type="checkbox"
                      class="h-3.5 w-3.5 rounded border-slate-600 bg-slate-900"
                      :checked="form.judge_model_ids.includes(item.id)"
                      @change="toggleJudge(item.id)"
                    />
                    {{ item.name }}
                  </label>
                  <span v-if="judgeModels.length === 0" class="text-xs text-slate-500">暂无裁判模型</span>
                </div>
              </div>

              <div class="w-40">
                <label class="label">通过分数线</label>
                <input v-model.number="form.pass_threshold" class="input" type="number" min="0" max="100" />
              </div>
            </section>

            <section v-if="isEdit" class="space-y-3">
              <h3 class="text-xs font-semibold uppercase tracking-wider text-indigo-300">变更说明</h3>
              <input v-model="form.change_summary" class="input" placeholder="例如：调整预期金额与裁判阈值" />
            </section>
          </div>

          <footer class="flex items-center justify-between gap-2 border-t border-slate-800 px-5 py-3">
            <p class="text-[11px] text-slate-500">
              {{ isEdit ? '保存会生成新版本快照' : '创建后版本号为 v1' }}
            </p>
            <div class="flex gap-2">
              <button class="btn-ghost" @click="close">取消</button>
              <button class="btn-primary" :disabled="saving" @click="submit">
                {{ saving ? '保存中…' : isEdit ? '保存新版本' : '创建用例' }}
              </button>
            </div>
          </footer>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.drawer-enter-active,
.drawer-leave-active {
  transition: opacity 0.2s ease;
}
.drawer-enter-from,
.drawer-leave-to {
  opacity: 0;
}
.drawer-enter-active aside,
.drawer-leave-active aside {
  transition: transform 0.22s ease;
}
.drawer-enter-from aside,
.drawer-leave-to aside {
  transform: translateX(100%);
}
</style>

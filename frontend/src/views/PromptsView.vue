<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../api'
import BaseModal from '../components/BaseModal.vue'
import EmptyState from '../components/EmptyState.vue'
import JsonViewer from '../components/JsonViewer.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import PromptAssetCenter from './components/PromptAssetCenter.vue'
import type {
  ModelConfig,
  ModelRole,
  PromptListItem,
  PromptTemplate,
  PromptUsage,
  SandboxResponse,
  TemplateType,
} from '../types'
import { useConfirm } from '../composables/useConfirm'
import { errorMessage } from '../utils/errors'
import { parseJsonObject, templateTypeLabel } from '../utils/format'
import { matchesAnyKeyword } from '../utils/search'
import { useToastStore } from '../stores/toast'
import { useAuthStore } from '../stores/auth'
import SelectButton from '../components/SelectButton.vue'

const toast = useToastStore()
const auth = useAuthStore()
const confirm = useConfirm()

const templates = ref<PromptListItem[]>([])
const models = ref<ModelConfig[]>([])
const selectedId = ref<number | null>(null)
const detail = ref<PromptTemplate | null>(null)
const loadingList = ref(false)
const loadingDetail = ref(false)
const keyword = ref('')
const typeFilter = ref('')

const createOpen = ref(false)
const creating = ref(false)
const createForm = reactive({
  code: '',
  name: '',
  template_type: 'USER_SIMULATOR' as TemplateType,
  description: '',
  content: '',
  changelog: '初始版本',
})

const versionOpen = ref(false)
const publishing = ref(false)
const versionForm = reactive({ content: '', changelog: '' })

const usageOpen = ref(false)
const usageLoading = ref(false)
const usage = ref<PromptUsage | null>(null)

const metaOpen = ref(false)
const savingMeta = ref(false)
const metaForm = reactive({ name: '', description: '', is_active: true })

const sandboxOpen = ref(false)
const runningSandbox = ref(false)
const sandboxResult = ref<SandboxResponse | null>(null)

type SandboxMode = 'SIMULATOR' | 'JUDGE' | 'CASE_AUTHOR' | 'SELF_CHECK'

// 沙盒试跑模式与模型角色一一对应，切换模式时只展示同角色模型。
// SELF_CHECK 不调用平台模型（仅渲染预览），沿用裁判角色做占位。
const SANDBOX_MODE_ROLE: Record<SandboxMode, ModelRole> = {
  SIMULATOR: 'SIMULATOR',
  JUDGE: 'JUDGE',
  CASE_AUTHOR: 'CASE_AUTHOR',
  SELF_CHECK: 'JUDGE',
}

const sandboxForm = reactive({
  mode: 'SIMULATOR' as SandboxMode,
  execution_id: null as number | null,
  model_id: null as number | null,
  variablesText: '',
  contentOverride: '',
  temperature: null as number | null,
})

const filteredTemplates = computed(() =>
  templates.value.filter((item) => matchesAnyKeyword(keyword.value, [item.code, item.name])),
)

// 沙盒模型与执行链路同一套注册数据：按当前模式过滤角色，避免选到跨角色模型；
// 按 id 升序排列，使缺省项与后端「id 最小的启用模型」规则一致。
const sandboxModels = computed(() =>
  models.value
    .filter(
      (item) => item.is_active && item.role === SANDBOX_MODE_ROLE[sandboxForm.mode],
    )
    .slice()
    .sort((a, b) => a.id - b.id),
)

function pickSandboxModel(): void {
  sandboxForm.model_id = sandboxModels.value[0]?.id ?? null
}

// 切换「模拟器 / 裁判」模式时同步切换角色默认模型，避免残留跨角色 id。
watch(
  () => sandboxForm.mode,
  () => pickSandboxModel(),
)

function defaultVariables(mode: SandboxMode): string {
  if (mode === 'SIMULATOR') {
    return JSON.stringify(
      {
        persona_description: '某科技公司财务，关注账单金额与明细',
        user_goal: '拿到订单 A2024001 的含税金额与明细笔数',
        hidden_context: '- order_no: A2024001\n- period: 2026-10',
        current_turn: 1,
        max_turns: 5,
        chat_history: '（暂无对话）',
      },
      null,
      2,
    )
  }
  if (mode === 'CASE_AUTHOR') {
    return JSON.stringify(
      {
        user_request: '帮我生成一条本月账单金额核对的测试用例',
        mode: 'CREATE',
        count: '1',
        calibers: '（暂无业务口径资产）',
        similar_cases: '（暂无相似历史用例）',
      },
      null,
      2,
    )
  }
  if (mode === 'SELF_CHECK') {
    return JSON.stringify(
      {
        case_name: '（未选择历史执行）',
        case_type: 'SINGLE',
        judge_total_score: 0,
        pass_threshold: 60,
        termination_status: 'COMPLETED',
        actual_turns: 0,
        max_turns: 1,
        judge_error_tags: '（无）',
        judge_reasons: '（未产出裁判理由，可绑定一条历史失败执行回放真实理由）',
        rule_failures: '（本用例未执行规则硬校验）',
        expected_answer: '（未提供）',
        first_query: '（未选择历史执行）',
        chat_history: '（暂无对话）',
        final_answer: '（无回复）',
      },
      null,
      2,
    )
  }
  return JSON.stringify(
    {
      case_type: 'SINGLE',
      chat_history: '（暂无对话）',
      raw_db_data: '（无）',
      expected_answer: '（未提供）',
      actual_turns: 0,
      max_turns: 1,
      termination_status: 'COMPLETED',
    },
    null,
    2,
  )
}

async function loadModels(): Promise<void> {
  try {
    const page = await api.models.list({ page_size: 200 })
    models.value = page.items
  } catch (exc) {
    toast.error(errorMessage(exc))
  }
}

async function loadTemplates(): Promise<boolean> {
  loadingList.value = true
  try {
    const page = await api.prompts.list({
      page_size: 200,
      template_type: (typeFilter.value || undefined) as
        | 'USER_SIMULATOR'
        | 'LLM_JUDGE'
        | 'CASE_AUTHOR'
        | 'SELF_CHECK'
        | undefined,
      keyword: keyword.value.trim() || undefined,
    })
    templates.value = page.items
    const stillExists = page.items.some((item) => item.id === selectedId.value)
    if (!stillExists) {
      const first = page.items[0]
      selectedId.value = first?.id ?? null
      if (first) await loadDetail(first.id)
      else detail.value = null
    }
    return true
  } catch (exc) {
    toast.error(errorMessage(exc))
    return false
  } finally {
    loadingList.value = false
  }
}

async function refreshTemplates(): Promise<void> {
  if (await loadTemplates()) toast.success('刷新成功')
}

async function loadDetail(templateId: number): Promise<void> {
  loadingDetail.value = true
  try {
    detail.value = await api.prompts.get(templateId)
  } catch (exc) {
    toast.error(errorMessage(exc))
    detail.value = null
  } finally {
    loadingDetail.value = false
  }
}

async function selectTemplate(templateId: number): Promise<void> {
  selectedId.value = templateId
  await loadDetail(templateId)
}

function openCreate(): void {
  createForm.code = ''
  createForm.name = ''
  createForm.template_type = 'USER_SIMULATOR'
  createForm.description = ''
  createForm.content = ''
  createForm.changelog = '初始版本'
  createOpen.value = true
}

async function submitCreate(): Promise<void> {
  if (!/^[A-Za-z0-9_-]+$/.test(createForm.code.trim())) {
    toast.error('模板编码只能包含字母、数字、下划线与短横线')
    return
  }
  if (!createForm.name.trim()) {
    toast.error('请填写模板名称')
    return
  }
  if (createForm.content.trim().length < 10) {
    toast.error('模板内容至少 10 个字符')
    return
  }
  creating.value = true
  try {
    const created = await api.prompts.create({
      code: createForm.code.trim(),
      name: createForm.name.trim(),
      template_type: createForm.template_type,
      description: createForm.description.trim() || null,
      content: createForm.content,
      changelog: createForm.changelog.trim() || null,
    })
    toast.success(`模板 ${created.code} 已创建`)
    createOpen.value = false
    await loadTemplates()
    await selectTemplate(created.id)
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    creating.value = false
  }
}

function openVersion(): void {
  if (!detail.value) return
  const current = detail.value.versions.find((item) => item.version === detail.value?.current_version)
  versionForm.content = current?.content ?? ''
  versionForm.changelog = ''
  versionOpen.value = true
}

async function submitVersion(): Promise<void> {
  if (!detail.value) return
  if (versionForm.content.trim().length < 10) {
    toast.error('模板内容至少 10 个字符')
    return
  }
  publishing.value = true
  try {
    const updated = await api.prompts.createVersion(
      detail.value.id,
      versionForm.content,
      versionForm.changelog.trim() || null,
    )
    detail.value = updated
    versionOpen.value = false
    await loadTemplates()
    toast.success(`已发布 v${updated.current_version}`)
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    publishing.value = false
  }
}

async function openUsage(): Promise<void> {
  if (!detail.value) return
  usageOpen.value = true
  usageLoading.value = true
  usage.value = null
  try {
    usage.value = await api.prompts.usage(detail.value.id)
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    usageLoading.value = false
  }
}

function openMeta(): void {
  if (!detail.value) return
  metaForm.name = detail.value.name
  metaForm.description = detail.value.description ?? ''
  metaForm.is_active = detail.value.is_active
  metaOpen.value = true
}

async function submitMeta(): Promise<void> {
  if (!detail.value) return
  savingMeta.value = true
  try {
    const updated = await api.prompts.update(detail.value.id, {
      name: metaForm.name.trim(),
      description: metaForm.description.trim() || null,
      is_active: metaForm.is_active,
    })
    detail.value = updated
    metaOpen.value = false
    await loadTemplates()
    toast.success('模板信息已更新')
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    savingMeta.value = false
  }
}

async function removeTemplate(): Promise<void> {
  if (!detail.value) return
  const ok = await confirm({
    title: '删除模板',
    message: `确定删除模板 ${detail.value.code} 吗？`,
    detail: '内置模板或被引用模板无法删除。',
    confirmText: '删除',
    icon: 'trash',
  })
  if (!ok) return
  try {
    const result = await api.prompts.remove(detail.value.id)
    toast.success(result.message)
    selectedId.value = null
    detail.value = null
    await loadTemplates()
  } catch (exc) {
    toast.error(errorMessage(exc))
  }
}

function sandboxModeOfTemplate(type?: string | null): SandboxMode {
  if (type === 'USER_SIMULATOR') return 'SIMULATOR'
  if (type === 'CASE_AUTHOR') return 'CASE_AUTHOR'
  if (type === 'SELF_CHECK') return 'SELF_CHECK'
  return 'JUDGE'
}

function openSandbox(version?: number): void {
  if (!detail.value) return
  const target =
    detail.value.versions.find((item) => item.version === version) ??
    detail.value.versions.find((item) => item.version === detail.value?.current_version) ??
    detail.value.versions[0]
  sandboxForm.mode = sandboxModeOfTemplate(detail.value.template_type)
  sandboxForm.execution_id = null
  pickSandboxModel()
  sandboxForm.contentOverride = target?.content ?? ''
  sandboxForm.variablesText = defaultVariables(sandboxForm.mode)
  sandboxForm.temperature = null
  sandboxResult.value = null
  sandboxOpen.value = true
}

async function runSandbox(): Promise<void> {
  if (!detail.value) return
  let variables: Record<string, unknown> = {}
  if (sandboxForm.variablesText.trim()) {
    try {
      variables = parseJsonObject(sandboxForm.variablesText)
    } catch (exc) {
      toast.error(`变量 JSON 格式不正确：${errorMessage(exc, '')}`)
      return
    }
  }
  runningSandbox.value = true
  try {
    sandboxResult.value = await api.prompts.sandbox(detail.value.id, {
      mode: sandboxForm.mode,
      execution_id: sandboxForm.execution_id,
      model_id: sandboxForm.model_id,
      variables,
      content_override: sandboxForm.contentOverride.trim() || null,
      temperature: sandboxForm.temperature,
    })
    toast.success('沙盒执行完成')
  } catch (exc) {
    toast.error(errorMessage(exc))
  } finally {
    runningSandbox.value = false
  }
}

onMounted(() => {
  void loadModels()
  void loadTemplates()
})
</script>

<template>
  <div class="space-y-4">
    <PageHeader
      eyebrow="Assets · 提示词"
      title="Prompt 资产"
      description="用户模拟器、LLM 裁判、AI 用例编写与失败追问自检模板的版本管理、引用影响分析与沙盒试跑。"
    >
      <template #actions>
        <button v-if="auth.can('prompt:write')" class="btn-primary" @click="openCreate">
          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" d="M12 5v14M5 12h14" />
          </svg>
          新建模板
        </button>
      </template>
    </PageHeader>

    <div class="grid gap-5 lg:grid-cols-[320px_minmax(0,1fr)]">
      <section class="card flex max-h-[720px] flex-col overflow-hidden">
        <div class="space-y-2 border-b border-slate-200 p-3">
          <input v-model="keyword" class="input !py-1.5 text-xs" placeholder="搜索编码 / 名称（分号分隔多词）" @keyup.enter="loadTemplates" />
          <div class="flex gap-2">
            <select v-model="typeFilter" class="input !py-1.5 text-xs" @change="loadTemplates">
              <SelectButton />
              <option value="">全部类型</option>
              <option value="USER_SIMULATOR">用户模拟器</option>
              <option value="LLM_JUDGE">LLM 裁判</option>
              <option value="CASE_AUTHOR">AI 用例编写</option>
              <option value="SELF_CHECK">失败追问自检</option>
            </select>
            <button class="btn-ghost btn-xs shrink-0" @click="refreshTemplates">刷新</button>
          </div>
          <p class="text-[11px] text-slate-500">共 {{ filteredTemplates.length }} 条</p>
        </div>
        <div class="flex-1 overflow-y-auto">
          <button
            v-for="item in filteredTemplates"
            :key="item.id"
            class="w-full border-b border-slate-200 px-4 py-3 text-left transition hover:bg-slate-100/70"
            :class="item.id === selectedId ? 'bg-indigo-50' : ''"
            @click="selectTemplate(item.id)"
          >
            <div class="flex items-center justify-between gap-2">
              <span class="truncate text-sm font-medium text-slate-800">{{ item.name }}</span>
              <StatusPill :status="item.is_active ? 'ACTIVE' : 'DISABLED'" size="sm" />
            </div>
            <p class="mt-1 font-mono text-[10px] text-slate-600">{{ item.code }}</p>
            <div class="mt-1.5 flex flex-wrap items-center gap-2 text-[10px] text-slate-600">
              <span class="tag">{{ templateTypeLabel(item.template_type) }}</span>
              <span>v{{ item.current_version }}</span>
              <span>引用 {{ item.ref_count }}</span>
              <span v-if="item.is_builtin" class="text-indigo-600">内置</span>
            </div>
          </button>
          <EmptyState
            v-if="filteredTemplates.length === 0 && !loadingList"
            compact
            title="没有匹配的模板"
            description="调整筛选条件或新建模板。"
          />
        </div>
      </section>

      <PromptAssetCenter
        :template="detail"
        :loading="loadingDetail"
        @create-version="openVersion"
        @view-usage="openUsage"
        @sandbox="openSandbox"
        @edit-meta="openMeta"
        @delete="removeTemplate"
      />
    </div>

    <BaseModal v-model="createOpen" title="新建 Prompt 模板" width="lg">
      <div class="space-y-3">
        <div class="grid gap-3 sm:grid-cols-2">
          <div>
            <label class="label">模板编码</label>
            <input v-model="createForm.code" class="input font-mono" placeholder="SIM_MY_SCENARIO" />
          </div>
          <div>
            <label class="label">模板名称</label>
            <input v-model="createForm.name" class="input" placeholder="我的模拟器模板" />
          </div>
        </div>
        <div>
          <label class="label">模板类型</label>
          <select v-model="createForm.template_type" class="input">
            <SelectButton />
            <option value="USER_SIMULATOR">用户模拟器（动态多轮）</option>
            <option value="LLM_JUDGE">LLM 裁判（评分）</option>
            <option value="CASE_AUTHOR">AI 用例编写（草稿生成）</option>
            <option value="SELF_CHECK">失败追问自检（同会话追问）</option>
          </select>
        </div>
        <div>
          <label class="label">描述</label>
          <input v-model="createForm.description" class="input" placeholder="用途与适用场景" />
        </div>
        <div>
          <label class="label">模板内容</label>
          <textarea v-model="createForm.content" class="input font-mono text-xs" rows="10" placeholder="支持 {{variable}} 占位符" />
        </div>
        <div>
          <label class="label">变更说明</label>
          <input v-model="createForm.changelog" class="input" placeholder="初始版本" />
        </div>
      </div>
      <template #footer>
        <button class="btn-ghost" @click="createOpen = false">取消</button>
        <button class="btn-primary" :disabled="creating" @click="submitCreate">
          {{ creating ? '创建中…' : '创建模板' }}
        </button>
      </template>
    </BaseModal>

    <BaseModal
      v-model="versionOpen"
      :title="detail ? `发布新版本 · ${detail.code}` : '发布新版本'"
      subtitle="保存后版本号自增，引用 LATEST 的用例会自动使用新版本"
      width="lg"
    >
      <div class="space-y-3">
        <div>
          <label class="label">模板内容</label>
          <textarea v-model="versionForm.content" class="input font-mono text-xs" rows="14" />
        </div>
        <div>
          <label class="label">变更说明</label>
          <input v-model="versionForm.changelog" class="input" placeholder="例如：强化金额零容忍约束" />
        </div>
      </div>
      <template #footer>
        <button class="btn-ghost" @click="versionOpen = false">取消</button>
        <button class="btn-primary" :disabled="publishing" @click="submitVersion">
          {{ publishing ? '发布中…' : '发布新版本' }}
        </button>
      </template>
    </BaseModal>

    <BaseModal
      v-model="usageOpen"
      title="引用影响分析"
      :subtitle="detail ? detail.code : ''"
      width="lg"
    >
      <div v-if="usageLoading" class="py-8 text-center text-sm text-slate-500">加载引用用例…</div>
      <div v-else-if="usage" class="space-y-4">
        <div class="grid grid-cols-2 gap-3">
          <div class="rounded-xl border border-slate-200 bg-slate-50/80 px-3 py-2">
            <p class="text-[10px] uppercase tracking-wider text-slate-500">引用数量</p>
            <p class="text-lg font-semibold text-slate-900">{{ usage.ref_count }}</p>
          </div>
          <div class="rounded-xl border border-slate-200 bg-slate-50/80 px-3 py-2">
            <p class="text-[10px] uppercase tracking-wider text-slate-500">可删除</p>
            <p class="text-lg font-semibold" :class="usage.can_delete ? 'text-emerald-700' : 'text-rose-700'">
              {{ usage.can_delete ? '是' : '否' }}
            </p>
          </div>
        </div>
        <div v-if="usage.delete_blockers.length" class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
          <p v-for="blocker in usage.delete_blockers" :key="blocker">{{ blocker }}</p>
        </div>
        <div class="overflow-hidden rounded-xl border border-slate-200">
          <table class="table-shell">
            <thead>
              <tr>
                <th class="th">用例</th>
                <th class="th">引用方式</th>
                <th class="th">锁定版本</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in usage.cases" :key="`${item.id}-${item.strategy}`">
                <td class="td">
                  <p class="font-mono text-xs text-slate-700">{{ item.code }}</p>
                  <p class="text-[11px] text-slate-500">{{ item.name }}</p>
                </td>
                <td class="td">
                  <span class="tag">{{ item.strategy === 'PINNED' ? '锁定版本' : '跟随最新' }}</span>
                </td>
                <td class="td text-xs text-slate-600">{{ item.pinned_version ? `v${item.pinned_version}` : '—' }}</td>
              </tr>
            </tbody>
          </table>
          <EmptyState v-if="usage.cases.length === 0" compact title="暂无引用" description="该模板还没有被任何用例使用。" />
        </div>
      </div>
    </BaseModal>

    <BaseModal v-model="metaOpen" title="编辑模板信息" width="md">
      <div class="space-y-3">
        <div>
          <label class="label">名称</label>
          <input v-model="metaForm.name" class="input" />
        </div>
        <div>
          <label class="label">描述</label>
          <textarea v-model="metaForm.description" class="input" rows="2" />
        </div>
        <label class="flex items-center gap-2 text-sm text-slate-700">
          <input v-model="metaForm.is_active" type="checkbox" class="h-4 w-4 rounded border-slate-300 bg-white" />
          启用该模板
        </label>
      </div>
      <template #footer>
        <button class="btn-ghost" @click="metaOpen = false">取消</button>
        <button class="btn-primary" :disabled="savingMeta" @click="submitMeta">
          {{ savingMeta ? '保存中…' : '保存' }}
        </button>
      </template>
    </BaseModal>

    <BaseModal
      v-model="sandboxOpen"
      :title="detail ? `沙盒试用 · ${detail.code}` : '沙盒试用'"
      subtitle="渲染模板变量并按角色试跑；自检模板仅做渲染预览，不调用模型"
      width="xl"
    >
      <div class="grid gap-4 lg:grid-cols-2">
        <div class="space-y-3">
          <div class="grid gap-3 sm:grid-cols-2">
            <div>
              <label class="label">执行角色</label>
              <select v-model="sandboxForm.mode" class="input">
                <SelectButton />
                <option value="SIMULATOR">用户模拟器</option>
                <option value="JUDGE">LLM 裁判</option>
                <option value="CASE_AUTHOR">AI 用例编写</option>
                <option value="SELF_CHECK">失败追问自检</option>
              </select>
            </div>
            <div v-if="sandboxForm.mode !== 'SELF_CHECK'">
              <label class="label">使用模型</label>
              <select v-model="sandboxForm.model_id" class="input">
                <SelectButton />
                <option :value="null">按角色默认</option>
                <option v-for="item in sandboxModels" :key="item.id" :value="item.id">
                  {{ item.name }}（{{ item.model_name }}）
                </option>
              </select>
            </div>
          </div>
          <div class="grid gap-3 sm:grid-cols-2">
            <div v-if="sandboxForm.mode !== 'CASE_AUTHOR'">
              <label class="label">历史执行 ID（可选）</label>
              <input v-model.number="sandboxForm.execution_id" class="input" type="number" min="1" placeholder="从历史执行继承变量" />
            </div>
            <div>
              <label class="label">温度覆盖（可选）</label>
              <input v-model.number="sandboxForm.temperature" class="input" type="number" min="0" max="2" step="0.1" placeholder="默认" />
            </div>
          </div>
          <div>
            <label class="label">变量 JSON</label>
            <textarea v-model="sandboxForm.variablesText" class="input font-mono text-xs" rows="9" />
            <p class="hint">
              {{
                sandboxForm.mode === 'CASE_AUTHOR'
                  ? '留空则使用平台默认变量：用户需求、编写模式、业务口径与相似历史用例。'
                  : sandboxForm.mode === 'SELF_CHECK'
                    ? '自检模板仅做渲染预览（不调用模型）；填写历史执行 ID 可回放真实裁判理由与对话上下文。'
                    : '留空则使用平台默认变量；填写历史执行 ID 时，会先按该次执行快照构建变量。'
              }}
            </p>
          </div>
          <div>
            <label class="label">模板内容（可临时覆盖）</label>
            <textarea v-model="sandboxForm.contentOverride" class="input font-mono text-xs" rows="8" />
          </div>
          <button class="btn-primary w-full" :disabled="runningSandbox" @click="runSandbox">
            {{ runningSandbox ? '执行中…' : '运行沙盒' }}
          </button>
        </div>

        <div class="space-y-3">
          <template v-if="sandboxResult">
            <div class="grid grid-cols-3 gap-2">
              <div class="rounded-lg border border-slate-200 bg-slate-50/80 px-2 py-1.5">
                <p class="text-[10px] text-slate-500">时延</p>
                <p class="text-sm font-semibold text-slate-800">{{ sandboxResult.latency_ms.toFixed(0) }} ms</p>
              </div>
              <div class="rounded-lg border border-slate-200 bg-slate-50/80 px-2 py-1.5">
                <p class="text-[10px] text-slate-500">Tokens</p>
                <p class="text-sm font-semibold text-slate-800">
                  {{ sandboxResult.prompt_tokens }} / {{ sandboxResult.completion_tokens }}
                </p>
              </div>
              <div class="rounded-lg border border-slate-200 bg-slate-50/80 px-2 py-1.5">
                <p class="text-[10px] text-slate-500">模型</p>
                <p class="truncate text-xs font-medium text-slate-800">{{ sandboxResult.model_name }}</p>
              </div>
            </div>
            <div
              v-if="sandboxResult.missing_variables.length"
              class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700"
            >
              缺失变量：{{ sandboxResult.missing_variables.join(', ') }}
            </div>
            <div>
              <p class="label">渲染后的 Prompt</p>
              <pre class="mono max-h-52 overflow-auto whitespace-pre-wrap rounded-xl border border-slate-200 bg-slate-50 p-3 text-slate-700">{{ sandboxResult.rendered_prompt }}</pre>
            </div>
            <div>
              <p class="label">解析输出</p>
              <JsonViewer :value="sandboxResult.output" max-height="200px" label="结构化 JSON" />
            </div>
            <details class="rounded-xl border border-slate-200 bg-slate-50 p-3">
              <summary class="cursor-pointer text-xs text-slate-600">原始模型输出</summary>
              <pre class="mono mt-2 max-h-52 overflow-auto whitespace-pre-wrap text-slate-600">{{ sandboxResult.raw_content }}</pre>
            </details>
          </template>
          <EmptyState
            v-else
            title="等待运行"
            description="左侧调整变量与模板内容，点击「运行沙盒」查看渲染 Prompt 与模型输出。"
          />
        </div>
      </div>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
/**
 * 一轮的完整档案抽屉：本轮 / 判定 / 执行 / 证据 / 派生与上下文。
 * 只渲染后端真实字段；缺失处写明「未记录」，不补造分数、报文与时延。
 */
import { computed, ref, watch } from 'vue'
import type { ExecutionDetail } from '../../../types'
import {
  PASS_LINE_SOURCE_LABEL,
  SCOPE_META,
  TAG_TONE,
  VERDICT_META,
  finishReasonOf,
  fmtMs,
  fmtScore,
  scoreLabel,
  type TurnVM,
  type TraceModel,
} from './model'
import { inputSourceText, pretty, statusText as statusLabelOf } from './format'
import { copyText } from '../../../utils/clipboard'
import { useToastStore } from '../../../stores/toast'
import ChainList from './ChainList.vue'
import CopyBox from './CopyBox.vue'
import TraceIcon from './TraceIcon.vue'
import type { DrawerSection, EvidTab } from './useTraceView'

const props = defineProps<{
  open: boolean
  turn: TurnVM | null
  turns: TurnVM[]
  model: TraceModel
  detail: ExecutionDetail | null
  section: DrawerSection
  evidTab: EvidTab
  focusTag: string | null
  openRows: Set<string>
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'section', value: DrawerSection): void
  (e: 'evid-tab', value: EvidTab): void
  (e: 'switch-turn', value: number): void
  (e: 'toggle-row', key: string): void
  (e: 'page', value: 'selfcheck'): void
  (e: 'focus-tag', tag: string, index: number): void
}>()

const SECTIONS: Array<{ key: DrawerSection; label: string; icon: string }> = [
  { key: 'turn', label: '本轮', icon: 'layers' },
  { key: 'judge', label: '判定', icon: 'scale' },
  { key: 'exec', label: '执行', icon: 'route' },
  { key: 'evid', label: '证据', icon: 'code' },
  { key: 'context', label: '派生与上下文', icon: 'refresh' },
]

const verdict = computed(() => VERDICT_META[props.turn?.verdict ?? 'fail'])
/** 判定卡标题：契约由规则闸门算，不是 LLM 裁判；LEGACY 只有裁判返回落库时才标 LLM */
const judgeCardTitle = computed(() => {
  if (props.model.gradingMode === 'ANSWER_CONTRACT') return '判定结论'
  const raw = props.detail?.judge_result
  return raw && typeof raw === 'object' && !Array.isArray(raw) ? 'LLM 裁判结论' : '判定结论'
})
const scoreInfo = computed(() => scoreLabel(props.model.gradingMode))
/** 通过线来自执行记录（judge_result.threshold / answer_grading.pass_threshold）或用例快照，不再前端写死 */
const passLine = computed(() => props.model.passLine)
const passLineSource = computed(() => PASS_LINE_SOURCE_LABEL[props.model.passLineSource])
const finishReason = computed(() => finishReasonOf(props.turn?.turn))
const multiTurn = computed(() => props.turns.length > 1)
const hasSql = computed(() => Boolean(props.turn?.sql))

const evidenceTabs = computed<Array<{ key: EvidTab; label: string; group: number }>>(() => {
  const tabs: Array<{ key: EvidTab; label: string; group: number }> = [
    { key: 'req', label: '请求', group: 0 },
    { key: 'res', label: '响应', group: 0 },
    { key: 'follow', label: '追问', group: 0 },
    { key: 'clarify', label: '反问', group: 0 },
    { key: 'chain', label: 'Agent 调用链', group: 1 },
  ]
  if (hasSql.value) tabs.push({ key: 'sql', label: 'SQL', group: 1 })
  tabs.push({ key: 'judge', label: '流程裁决', group: 2 })
  return tabs
})

const rulesPassed = computed(() => (props.turn?.rules ?? []).filter((rule) => rule.pass).length)
const factsPassed = computed(() => (props.turn?.facts ?? []).filter((fact) => fact.pass).length)
/** 轮次级事实缺失时回落到轮次级契约计数（后端多轮通常只有执行级契约，缺失时显示 —） */
const contractStat = computed<{ text: string; warn: boolean }>(() => {
  const turn = props.turn
  if (!turn) return { text: '—', warn: false }
  if (turn.facts.length) {
    return { text: `${factsPassed.value} / ${turn.facts.length}`, warn: factsPassed.value < turn.facts.length }
  }
  if (turn.contract) {
    return {
      text: `${turn.contract.passed} / ${turn.contract.total}`,
      warn: turn.contract.passed < turn.contract.total,
    }
  }
  return { text: '—', warn: false }
})
/** 轮次级四维缺失时回落执行级四维（数字真实，但卡片头会标注「执行级口径 · 不拆轮次」） */
const drawerDims = computed(() =>
  props.turn?.dimensions.length ? props.turn.dimensions : props.model.dimensions,
)
/** 这份四维是不是执行级回落的（多轮全量如此，后端没有轮次级四维字段） */
const dimsFromExec = computed(
  () => !(props.turn?.dimensions.length ?? 0) && props.model.dimensions.length > 0,
)
const dimensionTotal = computed(() =>
  drawerDims.value.reduce((sum, dim) => sum + dim.value, 0),
)
/** 四维合计的比对基准：有轮次分比轮次分，多轮回落到执行级综合分 */
const dimsCompareScore = computed(() => props.turn?.score ?? props.model.totalScore)
const slowest = computed(() => {
  // 后端没落时延的步骤不参与「最慢」评选，也不按 0ms 垫底
  const steps = (props.turn?.steps ?? []).filter((step) => typeof step.ms === 'number')
  if (!steps.length) return null
  return steps.reduce((acc, step) => ((step.ms ?? 0) > (acc.ms ?? 0) ? step : acc), steps[0])
})
const stepTotalMs = computed<number | null>(() => {
  const timed = (props.turn?.steps ?? [])
    .map((step) => step.ms)
    .filter((value): value is number => typeof value === 'number')
  return timed.length ? timed.reduce((sum, value) => sum + value, 0) : null
})
const stepWarnCount = computed(() => (props.turn?.steps ?? []).filter((step) => step.status === 'warn').length)
/** 异常步骤去重后的工具名，用于执行段的琥珀色提示，不编造未出现的工具 */
const warnStepNames = computed(() => {
  const names = (props.turn?.steps ?? [])
    .filter((step) => step.status === 'warn')
    .map((step) => step.tool)
    .filter(Boolean)
  return [...new Set(names)].slice(0, 3).join(' / ') || '未记录工具名'
})
const toolCount = computed(() => new Set((props.turn?.steps ?? []).map((step) => step.tool)).size)

const rawEntries = computed<Array<[string, string]>>(() => {
  const raw = props.turn?.turn.raw_db_data
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return []
  return Object.entries(raw as Record<string, unknown>).map(([key, value]) => [
    key,
    value === null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value),
  ])
})

const previousTurn = computed(() => {
  if (!props.turn) return null
  return props.turns.find((item) => item.index === props.turn!.index - 1) ?? null
})

const selfCheckTurn = computed(() => props.model.selfCheckTurn)
const selfRecord = computed(() => props.model.selfCheck)
const selfSkipReason = computed(() => props.model.selfCheckSkipReason)
// 通过的用例不展示任何自检相关内容；未通过的执行即使无记录也保留诚实空态
const executionPassed = computed(() => props.model.executionPassed)

const judgePrompt = computed(() => props.model.judges.find((judge) => judge.judge_prompt)?.judge_prompt ?? '')
const judgeRaw = computed(() => (props.model.judges[0]?.raw_response as unknown) ?? null)
const judgeModel = computed(() => {
  const names = props.model.judges.map((judge) => judge.model_name).filter(Boolean)
  return names.length ? [...new Set(names)].join(' / ') : '未记录'
})
const judgeTokens = computed(() =>
  props.model.judges.reduce((sum, judge) => sum + (judge.prompt_tokens ?? 0) + (judge.completion_tokens ?? 0), 0),
)
/** 本轮结论优先；多轮没有轮次级 reason 时明确标注为会话级结论 */
const judgeConclusion = computed(() => {
  const turn = props.turn
  if (!turn) return '裁判未给出结论'
  if (turn.judgeReason) return turn.judgeReason
  if (multiTurn.value && turn.sessionJudgeReason) return `（会话级，不归属本轮）${turn.sessionJudgeReason}`
  return '裁判未给出结论'
})
/** 裁判温度只在裁决原始返回里出现过才展示 */
const judgeTemperature = computed<number | null>(() => {
  const raw = judgeRaw.value as Record<string, unknown> | null
  const value = raw && typeof raw === 'object' ? raw.temperature : null
  return typeof value === 'number' ? value : null
})
/** 轮次级 + 会话级归因是否都落在已知归因枚举内（对应「裁决返回校验」） */
const tagsInEnum = computed(() =>
  [...(props.turn?.tags ?? []), ...(props.turn?.sessionTags ?? [])].every((tag) =>
    Object.prototype.hasOwnProperty.call(TAG_TONE, tag),
  ),
)

/** 请求 ID 只在报文头里出现过才展示，取不到时由模板降级为「未记录」 */
const requestId = computed(() => {
  const pick = (list: Array<[string, string]>) =>
    list.find(([key]) => key.toLowerCase() === 'x-request-id')?.[1] ?? ''
  return pick(props.turn?.request.headers ?? []) || pick(props.turn?.responseHeaders ?? []) || ''
})
/** 采样参数来自请求体，缺字段时整项降级，不编造 0 */
const samplingLabel = computed(() => {
  const body = props.turn?.request.body
  if (!body || typeof body !== 'object' || Array.isArray(body)) return ''
  const record = body as Record<string, unknown>
  const temperature = record.temperature
  const maxTokens = record.max_tokens ?? record.maxTokens
  if (temperature == null && maxTokens == null) return ''
  return `${temperature ?? '—'} / ${maxTokens ?? '—'}`
})
/** 被测模型的名称 / stream / response_format 同样只从请求体读，缺失就降级 */
const requestRecord = computed<Record<string, unknown> | null>(() => {
  const body = props.turn?.request.body
  return body && typeof body === 'object' && !Array.isArray(body) ? (body as Record<string, unknown>) : null
})
const agentModelLabel = computed(() => {
  const value = requestRecord.value?.model ?? requestRecord.value?.model_name ?? requestRecord.value?.modelName
  return typeof value === 'string' ? value : ''
})
const streamLabel = computed(() => {
  const value = requestRecord.value?.stream
  return typeof value === 'boolean' ? String(value) : ''
})
const responseFormatLabel = computed(() => {
  const value = requestRecord.value?.response_format
  if (value == null) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'object') {
    const type = (value as Record<string, unknown>).type
    return typeof type === 'string' ? type : JSON.stringify(value)
  }
  return String(value)
})
/** 回复正文里的内联图标标记数（正文渲染时会移除标记，这里单独计数） */
const inlineIconCount = computed(() => (props.turn?.answer.match(/:icon:[a-z-]+:/gi) ?? []).length)

const traceUid = computed(() => props.detail?.trace_uid ?? '')
const executionLabel = computed(() => (props.detail ? `#${props.detail.id}` : '—'))
const caseLabel = computed(() => {
  const detail = props.detail
  if (!detail) return '—'
  const code = detail.case_code || ''
  const name = detail.case_name || ''
  if (code && name) return `${code} · ${name}`
  return code || name || `用例 #${detail.case_id}`
})
const envLabel = computed(() => props.detail?.environment_name || '未记录')
const sourceLabel = computed(() => inputSourceText(props.turn?.source))
const timeLabel = computed(() => (props.turn?.at ? props.turn.at.replace('T', ' ').slice(0, 19) : '—'))

const sessionAvg = computed(() => {
  const scores = props.turns.map((item) => item.score).filter((value): value is number => value !== null)
  if (!scores.length) return null
  return scores.reduce((a, b) => a + b, 0) / scores.length
})

/** 会话级口径：unknown 既不算通过也不算失败，避免多轮无判定时臆造「通过」。 */
const decidedTurns = computed(() => props.turns.filter((item) => item.verdict !== 'unknown'))

/** 执行级结论：`pass_flag` 是后端权威值；全轮未判定时也要给出执行级结论，不能显示「未判定」。 */
const execPassed = computed<boolean | null>(() => {
  if (typeof props.detail?.pass_flag === 'boolean') return props.detail.pass_flag
  return null
})
/** 会话级口径：有 pass_flag 时跟执行级结论；没有时才按轮次聚合，unknown 不算通过也不失败。 */
const sessionVerdictLabel = computed(() => {
  if (execPassed.value !== null) {
    const base = execPassed.value ? '通过' : '未通过'
    return decidedTurns.value.length ? base : `${base}（执行级）`
  }
  const decided = decidedTurns.value
  if (!decided.length) return '未判定'
  const passed = decided.filter((item) => item.pass).length
  if (passed === decided.length) return '通过'
  if (passed === 0) return '未通过'
  return '部分通过'
})
const sessionScoreTotal = computed(() => {
  const scored = decidedTurns.value.filter((item) => item.score !== null)
  if (!scored.length) return ''
  const sum = scored.reduce((acc, item) => acc + (item.score ?? 0), 0)
  return `${fmtScore(sum)} / ${scored.length * 100}`
})
/** 四维只对有轮次级四维的轮次取均值；后端不提供轮次四维时降级为「—」。 */
const sessionDimText = computed(() => {
  const withDims = props.turns.filter((item) => item.dimensions.length > 0)
  if (!withDims.length) return ''
  return withDims[0].dimensions
    .map((_, index) => {
      const sum = withDims.reduce((acc, item) => acc + (item.dimensions[index]?.value ?? 0), 0)
      return fmtScore(sum / withDims.length)
    })
    .join(' / ')
})
/** 会话级归因没有轮次归属：抽屉里只展示去重后的标签，不挂 T 号、不做跳转。 */
const sessionTagsAll = computed(() => [...new Set(props.turns.flatMap((item) => item.sessionTags))])

const tags = computed(() => props.turn?.tags ?? [])
const sessionTags = computed(() => props.turn?.sessionTags ?? [])
const clarifyTag = computed(() => (props.turn?.sessionTags ?? []).includes('未主动反问'))
/** 本轮意图本身是澄清：Agent 已经反问，不能显示成「本轮未发生反问」。 */
const isClarifyTurn = computed(() => (props.turn?.intent ?? '').toUpperCase() === 'CLARIFY')
const clarifyState = computed(() => {
  if (clarifyTag.value) return '归因命中 · 未主动反问'
  if (isClarifyTurn.value) return '本轮已反问'
  return '本轮未命中'
})

function toneOf(tag: string): string {
  return TAG_TONE[tag] ?? 'slate'
}

function rowOpen(key: string): boolean {
  return props.openRows.has(key)
}

function toggleRow(key: string): void {
  emit('toggle-row', key)
}

const toast = useToastStore()

/** 复制「流程裁决」整段叙述（与原型顶部「复制」一致），原始报文另有各自复制 */
async function copyJudgeFlow(): Promise<void> {
  const turn = props.turn
  if (!turn) return
  const parts = [
    `流程裁决 · T${turn.index}`,
    `用户问题：${turn.query || '—'}`,
    `裁判模型：${judgeModel.value}${judgeTemperature.value !== null ? ` · temperature ${judgeTemperature.value}` : ''}`,
    `返回：pass = ${turn.pass} · ${props.model.gradingMode === 'ANSWER_CONTRACT' ? '质量分' : '综合分'} ${fmtScore(turn.score)} · 错误归因 ${tags.value.length + sessionTags.value.length} 条`,
    `结论：${judgeConclusion.value}`,
  ]
  if (judgePrompt.value) parts.push('', '--- 裁决 Prompt ---', judgePrompt.value)
  if (judgeRaw.value) parts.push('', '--- 裁判返回 ---', pretty(judgeRaw.value))
  const ok = await copyText(parts.join('\n'))
  if (ok) toast.success('已复制')
  else toast.error('复制失败')
}

/** 进入「证据 → 流程裁决」时默认展开裁判原始返回（与原型一致），每轮只自动展开一次，之后尊重用户折叠 */
const autoOpenedJudge = ref<number | null>(null)
watch(
  () => [props.section, props.evidTab, props.turn?.index] as const,
  () => {
    if (props.section !== 'evid' || props.evidTab !== 'judge') return
    const index = props.turn?.index ?? 0
    if (autoOpenedJudge.value === index) return
    autoOpenedJudge.value = index
    if (judgeRaw.value && !props.openRows.has('ejres')) emit('toggle-row', 'ejres')
  },
  { immediate: true },
)

function selfTriggerLabel(): string {
  const index = selfCheckTurn.value?.index
  return index ? `T${index}` : '本次执行'
}

/** 后端已落库触发轮次（turn_index）；定位不到（老记录未存且 trigger 无轮次号）时如实写「未记录（执行级）」 */
const selfTriggerValue = computed(() => {
  const index = selfCheckTurn.value?.index
  return index ? `T${index}` : '未记录（执行级）'
})
</script>

<template>
  <Teleport to="body">
    <div class="dimmer" :class="{ on: open }" @click="emit('close')" />
    <aside
      class="drawer"
      :class="{ on: open }"
      :aria-hidden="open ? 'false' : 'true'"
      role="dialog"
      aria-label="用例轮次详情"
    >
      <header class="dr-head">
        <div class="dr-top">
          <span class="dr-id">{{ turn ? `T${turn.index}` : 'T—' }}</span>
          <span v-if="turn" class="stat-pill" :class="verdict.tone">
            <TraceIcon
              :name="turn.verdict === 'pass' ? 'check' : turn.verdict === 'warn' ? 'alert' : turn.verdict === 'unknown' ? 'info' : 'x'"
              :size="10"
            />
            {{ verdict.label }}
          </span>
          <div class="dr-q">{{ turn?.query || '（本轮没有提问记录）' }}</div>
          <button type="button" class="dr-close" title="关闭" @click="emit('close')">
            <TraceIcon name="x" :size="13" />
          </button>
        </div>

        <div v-if="turn" class="dr-meta">
          <span v-if="turn.intent">意图 {{ turn.intent }}</span>
          <span v-if="turn.intent" class="dot">·</span>
          <span>TTFT {{ fmtMs(turn.ttft) }}</span>
          <span class="dot">·</span>
          <span>时延 {{ fmtMs(turn.latency) }}</span>
          <span class="dot">·</span>
          <span>{{ turn.steps.length }} 步</span>
          <span class="dot">·</span>
          <span>tokens {{ turn.tokens }}</span>
          <span class="dot">·</span>
          <span>{{ scoreInfo.label }} <b :class="{ low: turn.score === null || turn.score < passLine }">{{ fmtScore(turn.score) }}</b></span>
        </div>

        <div class="dr-turns">
          <span class="lbl">轮次对照</span>
          <button
            v-for="item in turns"
            :key="item.index"
            type="button"
            :class="[item.verdict, { on: item.index === turn?.index }]"
            :title="`切到 T${item.index} · ${VERDICT_META[item.verdict].label}`"
            @click="emit('switch-turn', item.index)"
          >
            <b>T{{ item.index }}</b>
            <span class="s">{{ item.score === null ? VERDICT_META[item.verdict].short : fmtScore(item.score) }}</span>
          </button>
        </div>

        <nav class="dr-seg">
          <button
            v-for="item in SECTIONS"
            :key="item.key"
            type="button"
            :class="{ on: section === item.key }"
            @click="emit('section', item.key)"
          >
            <TraceIcon :name="item.icon" :size="11" />
            {{ item.label }}
          </button>
        </nav>
      </header>

      <div v-if="!turn" class="dr-body">
        <div class="empty">没有可展示的轮次记录。</div>
      </div>

      <div v-else class="dr-body">
        <!-- ============================ 本轮 ============================ -->
        <template v-if="section === 'turn'">
          <section class="dsec">
            <h5>本轮结果 <span class="sp" /><span class="cnt">pass = {{ turn.verdict === 'unknown' ? '—' : turn.pass }}</span></h5>
            <div class="dgrid">
              <div class="dstat" :class="{ warn: turn.rules.length > 0 && rulesPassed < turn.rules.length }">
                <div class="dl">规则</div>
                <div class="dv">{{ turn.rules.length ? `${rulesPassed} / ${turn.rules.length}` : '—' }}</div>
              </div>
              <div class="dstat" :class="{ warn: contractStat.warn }">
                <div class="dl">{{ model.gradingMode === 'ANSWER_CONTRACT' ? '契约事实' : '契约' }}</div>
                <div class="dv">{{ contractStat.text }}</div>
              </div>
              <div class="dstat" :class="{ warn: turn.score !== null && !turn.pass }">
                <div class="dl">{{ model.gradingMode === 'ANSWER_CONTRACT' ? '质量分' : '裁判' }}</div>
                <div class="dv">{{ fmtScore(turn.score) }}<small v-if="turn.score !== null"> / 100</small></div>
              </div>
              <div class="dstat" :class="{ warn: tags.length > 0 }">
                <div class="dl">错误归因</div>
                <div class="dv">
                  {{ tags.length }} 条<small v-if="sessionTags.length"> · 会话 {{ sessionTags.length }}</small>
                </div>
              </div>
            </div>
            <div class="dquicks">
              <button type="button" @click="emit('section', 'judge')">
                <TraceIcon name="scale" :size="11" />
                <span>判定明细</span>
                <em>规则 / 契约 / 评分</em>
                <span class="go">›</span>
              </button>
              <button type="button" @click="emit('section', 'evid')">
                <TraceIcon name="code" :size="11" />
                <span>原始证据</span>
                <em>{{ evidenceTabs.length }} 个子页签</em>
                <span class="go">›</span>
              </button>
            </div>
            <div class="dsub">
              <template v-if="model.gradingMode === 'ANSWER_CONTRACT'">
                pass = 安全 / 行为 / 事实 / 附加 四项契约闸门全过；当前契约事实 {{ contractStat.text }}<template
                  v-if="contractStat.text === '—'"
                >（执行记录未提供事实明细）</template><template v-else>（{{ contractStat.warn ? '未全过' : '全过' }}）</template>，质量分仅参考，不参与本轮 pass。
              </template>
              <template v-else>
                <template v-if="turn.verdict === 'unknown'">
                  pass = 硬门槛全过 且综合分 ≥ {{ passLine }}；本轮没有规则 / 契约门槛，也未提供轮次级分数，无法判定。
                </template>
                <template v-else-if="turn.score === null">
                  多轮执行未落库轮次级分数，本轮 pass 按已落库的硬门槛判定为「{{ turn.pass ? '通过' : '未通过' }}」，不参与分数比较。
                </template>
                <template v-else>pass = 硬门槛全过 且综合分 ≥ {{ passLine }}；本轮{{ turn.pass ? '满足。' : '不满足。' }}</template>
              </template>
              <template v-if="turn.score === null">{{ model.gradingMode === 'ANSWER_CONTRACT' ? '执行级质量分不拆分到本轮，因此分数显示 —。' : '执行级总分不拆分到本轮，因此分数显示 —。' }}</template>
              <template v-if="sessionTags.length"> 会话级归因 {{ sessionTags.length }} 条只作上下文，不计入本轮 pass。</template>
            </div>
          </section>

          <section class="dsec">
            <h5>运行标识 <span class="sp" /><span class="cnt">只读 · 已脱敏</span></h5>
            <div class="dkv">
              <div class="kv"><span class="k">会话</span><span class="v">{{ turn.sessionId || '未记录' }}</span></div>
              <div class="kv"><span class="k">用例</span><span class="v">{{ caseLabel }}</span></div>
              <div class="kv"><span class="k">执行</span><span class="v">{{ executionLabel }}</span></div>
              <div class="kv"><span class="k">轮次</span><span class="v">T{{ turn.index }} / 共 {{ turns.length }} 轮</span></div>
              <div class="kv"><span class="k">请求 ID</span><span class="v">{{ requestId || '未记录' }}</span></div>
              <div class="kv"><span class="k">trace</span><span class="v">{{ traceUid || '未记录' }}</span></div>
              <div class="kv"><span class="k">模型</span><span class="v">{{ agentModelLabel || '执行记录未保存被测模型名' }}</span></div>
              <div class="kv"><span class="k">温度 / 上限</span><span class="v">{{ samplingLabel || '未记录' }}</span></div>
              <div class="kv"><span class="k">输入来源</span><span class="v">{{ sourceLabel }}</span></div>
              <div class="kv"><span class="k">发生时间</span><span class="v">{{ timeLabel }}</span></div>
            </div>
          </section>

          <section class="dsec">
            <h5>输入 <span class="sp" /><span class="cnt">问题 · 变量 · 上文</span></h5>
            <div class="dkv one">
              <div class="kv"><span class="k">用户问题</span><span class="v">{{ turn.query || '—' }}</span></div>
              <div class="kv"><span class="k">上文摘要</span><span class="v">{{ previousTurn ? `承接 T${previousTurn.index}：${previousTurn.query}` : '首轮，无上文' }}</span></div>
            </div>
            <div v-if="turn.variables.length" class="dchips">
              <span v-for="([key, value], index) in turn.variables" :key="index"><b>{{ key }}</b> {{ value }}</span>
            </div>
            <div v-else class="dsub">本轮未携带请求变量</div>
            <div class="dsub">变量来自用例快照；多轮指代由会话上下文解析，原始 messages 见「证据 · 请求」。</div>
          </section>

          <section class="dsec">
            <h5>输出 <span class="sp" /><span class="cnt">回复正文与数据块</span></h5>
            <div class="dkv">
              <div class="kv"><span class="k">finish_reason</span><span class="v">{{ finishReason || '执行记录未保存' }}</span></div>
              <div class="kv"><span class="k">意图</span><span class="v">{{ turn.intent || '未记录' }}</span></div>
              <div class="kv"><span class="k">数据块</span><span class="v">{{ rawEntries.length ? rawEntries.map(([key]) => key).join(' · ') : '无' }}</span></div>
              <div class="kv"><span class="k">内联图标</span><span class="v">{{ inlineIconCount }} 个</span></div>
              <div class="kv">
                <span class="k">SQL</span>
                <span class="v">
                  <button v-if="turn.sql" type="button" class="lk" title="打开 SQL 全文" @click="emit('evid-tab', 'sql'); emit('section', 'evid')">
                    <TraceIcon name="code" :size="10" />
                    已生成 · {{ turn.sql.split('\n').length }} 行 ›
                  </button>
                  <template v-else>未生成</template>
                </span>
              </div>
            </div>
          </section>

          <section class="dsec">
            <h5>回复正文 <span class="sp" /><span class="cnt">{{ turn.answer.length }} 字</span></h5>
            <div class="dexpect">{{ turn.answer || '本轮没有回复正文（可能执行未完成或调用失败）。' }}</div>
          </section>

          <section v-if="rawEntries.length" class="dsec">
            <h5>
              本轮取数
              <span class="dsrc"><TraceIcon name="db" :size="10" />raw_db_data</span>
              <span class="sp" /><span class="cnt">{{ rawEntries.length }} 项</span>
            </h5>
            <div class="dtable-wrap">
              <table class="dtable">
                <thead><tr><th>字段</th><th>值</th></tr></thead>
                <tbody>
                  <tr v-for="([key, value], index) in rawEntries" :key="index">
                    <td class="mono">{{ key }}</td>
                    <td>{{ value }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="dsub">来自本轮工具返回的原始结构，未做口径换算。</div>
          </section>

          <section v-if="turn.expected" class="dsec">
            <h5>期望答案 <span class="sp" /><span class="cnt">{{ turn.expectedSource }}</span></h5>
            <div class="dexpect">{{ turn.expected }}</div>
          </section>
        </template>

        <!-- ============================ 判定 ============================ -->
        <template v-else-if="section === 'judge'">
          <!-- 判定页首屏就是最终结果：裁判怎么判、打多少分、基准是什么，收在同一张卡里。
               规则 / 契约 / 归因等佐证降级排在卡后，避免一打开先看到一串空态。 -->
          <section class="dsec">
            <div class="djudgecard" :class="`jv-${turn.verdict}`">
              <div class="djv-head">
                <span class="djv-ic"><TraceIcon name="scale" :size="12" /></span>
                <b class="djv-title">{{ judgeCardTitle }}</b>
                <span class="djv-model">{{ judgeModel }}</span>
                <span class="sp" />
                <span class="stat-pill" :class="verdict.tone">
                  <TraceIcon
                    :name="turn.verdict === 'pass' ? 'check' : turn.verdict === 'warn' ? 'alert' : turn.verdict === 'unknown' ? 'info' : 'x'"
                    :size="10"
                  />
                  {{ verdict.label }}
                </span>
                <span class="djv-score">
                  <b>{{ fmtScore(turn.score ?? model.totalScore) }}</b><em v-if="(turn.score ?? model.totalScore) !== null">/ 100</em>
                  <i>{{
                    turn.score !== null
                      ? model.gradingMode === 'ANSWER_CONTRACT'
                        ? '质量分参考'
                        : '本轮综合分'
                      : model.totalScore !== null
                        ? model.gradingMode === 'ANSWER_CONTRACT' ? '执行级质量分' : '执行级综合分'
                        : '无分'
                  }}</i>
                </span>
              </div>

              <div class="djv-body">
                <span v-if="!turn.judgeReason && multiTurn && turn.sessionJudgeReason" class="djv-src">会话级结论 · 未拆分到本轮</span>
                <p v-if="turn.judgeReason || (multiTurn && turn.sessionJudgeReason)">
                  {{ turn.judgeReason || turn.sessionJudgeReason }}
                </p>
                <p v-else class="muted">执行记录没有给出这一轮的裁判结论原文，不代裁判补写。</p>
                <div class="djv-meta">
                  <span>pass = {{ turn.verdict === 'unknown' ? '—' : turn.pass }}</span>
                  <template v-if="model.gradingMode === 'ANSWER_CONTRACT'">
                    <span>
                      契约门槛 {{ contractStat.text }}
                      <template v-if="contractStat.text !== '—'"> · {{ contractStat.warn ? '未全过' : '全过' }}</template>
                    </span>
                    <span>阈值 {{ passLine }}（随执行落库，不参与契约判定）</span>
                  </template>
                  <span v-else>阈值 {{ passLine }}（{{ passLineSource }}）</span>
                  <span
                    v-if="model.gradingMode !== 'ANSWER_CONTRACT' && !turn.pass && turn.score !== null && turn.score >= passLine"
                    class="flag"
                  >
                    分数已达标 · 硬门槛未全过
                  </span>
                  <span v-if="turn.score === null && turn.verdict !== 'unknown'" class="flag">本轮无独立分 · pass 仅依据{{ model.gradingMode === 'ANSWER_CONTRACT' ? '契约闸门' : '硬门槛' }}</span>
                  <span v-else-if="turn.score === null" class="flag">本轮无独立分 · 数字为执行级{{ model.gradingMode === 'ANSWER_CONTRACT' ? '质量分' : '综合分' }}</span>
                </div>
              </div>

              <div v-if="turn.expected" class="djv-basis">
                <div class="djv-basis-h">
                  <b>判定基准 · 期望答案</b>
                  <span class="sp" />
                  <span>{{ turn.expectedSource }}</span>
                </div>
                <p>{{ turn.expected }}</p>
                <div v-if="turn.expectedScope === 'session'" class="djv-note">
                  {{ multiTurn ? '用例级期望答案；多轮用例不会把它复用到每一轮。' : '用例级期望答案；本用例只有一轮，等同本轮判定基准。' }}
                </div>
              </div>

              <div v-if="drawerDims.length" class="djv-dims">
                <div class="djv-dims-h">
                  <b>{{ model.gradingMode === 'ANSWER_CONTRACT' ? '四维质量分' : '四维得分' }}</b>
                  <span class="sp" />
                  <span>{{ dimsFromExec ? '执行级口径 · 不拆轮次' : '轮次级口径' }}</span>
                </div>
                <div v-for="dim in drawerDims" :key="dim.key" class="dim">
                  <span>{{ dim.label }} <em>/{{ dim.max }}</em></span>
                  <div class="bar"><i :class="{ low: dim.value / dim.max < 0.8 }" :style="{ width: `${Math.min(100, (dim.value / dim.max) * 100)}%` }" /></div>
                  <b>{{ fmtScore(dim.value) }}<small>/{{ dim.max }}</small></b>
                </div>
                <div class="djv-note">
                  {{ scoreInfo.hint }}；四维合计 {{ fmtScore(dimensionTotal) }}，{{
                    dimsFromExec ? `是执行级${model.gradingMode === 'ANSWER_CONTRACT' ? '质量分' : '评分'}，不是本轮独立得分。` : `与本轮${model.gradingMode === 'ANSWER_CONTRACT' ? '质量分' : '综合分'}一致。`
                  }}
                </div>
              </div>
            </div>
          </section>

          <section class="dsec">
            <h5>规则校验 <span class="sp" /><span class="cnt">{{ turn.rules.length ? `${rulesPassed} / ${turn.rules.length}` : '无记录' }}</span></h5>
            <div v-if="turn.rules.length" class="rulelist">
              <div v-for="(rule, index) in turn.rules" :key="index" class="ruleline" :class="{ bad: !rule.pass }">
                <span class="ri"><TraceIcon :name="rule.pass ? 'check' : 'alert'" :size="11" /></span>
                <div>
                  {{ rule.name }}
                  <span v-if="rule.why" class="why">{{ rule.why }}</span>
                  <span v-if="rule.expected || rule.actual || rule.evidence" class="ra">
                    <em>期望</em>{{ rule.expected || '—' }} 
                    <em>{{ rule.actual ? '实际' : rule.evidence ? '命中片段' : '实际' }}</em><template v-if="rule.actual">{{ rule.actual }}</template><template v-else-if="rule.evidence">{{ rule.evidence }}</template><template v-else-if="rule.pass">已命中（后端未记录取值）</template><template v-else>—</template>
                  </span>
                </div>
              </div>
            </div>
            <div v-else class="dempty">本轮没有规则检查结果（rule_check 为空或未落库检查项），没有可展示的规则结果；界面不做推断。</div>
            <div v-if="isClarifyTurn" class="dsub">
              本轮意图为 CLARIFY：规则按「期望数值是否出现在回复里」校验，Agent 先反问、暂不给数值时会记为未命中，这不代表澄清动作本身有错。
            </div>
          </section>

          <section class="dsec">
            <h5>
              契约命中
              <span class="sp" />
              <span class="cnt">
                {{
                  turn.facts.length
                    ? `facts ${factsPassed} / ${turn.facts.length}`
                    : turn.contract
                      ? `契约 ${turn.contract.passed} / ${turn.contract.total}`
                      : '无记录'
                }}
              </span>
            </h5>
            <template v-if="turn.facts.length">
              <div class="contract">
                <div :class="{ bad: factsPassed < turn.facts.length }">facts<b>{{ factsPassed }} / {{ turn.facts.length }}</b></div>
                <div v-for="gate in model.contract.gates" :key="gate.key" :class="{ bad: !gate.passed }">
                  {{ gate.label }}<b>{{ gate.passed ? '通过' : '未通过' }}</b>
                </div>
              </div>
              <div class="rulelist">
                <div v-for="(fact, index) in turn.facts" :key="index" class="ruleline" :class="{ bad: !fact.pass }">
                  <span class="ri"><TraceIcon :name="fact.pass ? 'check' : 'alert'" :size="11" /></span>
                  <div>
                    {{ fact.label }}
                    <span v-if="fact.reason" class="why">{{ fact.reason }}</span>
                    <span v-if="fact.expected || fact.actual" class="ra">
                      <em>期望</em>{{ fact.expected }} <em>实际</em>{{ fact.actual }}
                    </span>
                  </div>
                </div>
              </div>
              <div v-if="turn.factsScope === 'execution'" class="dsub">
                契约结果只有执行级（最终答案）记录，没有 <code>turn_index</code>；这里挂在最后一轮展示，不拆分到其他轮次。
              </div>
            </template>
            <div v-else class="dempty">
              {{
                turn.contract
                  ? '答案契约按整通执行判定，未提供本轮事实明细。'
                  : model.gradingMode === 'ANSWER_CONTRACT'
                    ? '执行记录里没有契约事实结果（fact_results 为空）。'
                    : '当前为 LEGACY 判分模式，没有契约事实。'
              }}
            </div>
          </section>

          <section class="dsec">
            <h5>错误归因 <span class="sp" /><span class="cnt">轮次级 {{ tags.length }} · 会话级 {{ sessionTags.length }}</span></h5>
            <div class="attrib">
              <div class="jtagrow">
                <span class="jscope">轮次级</span>
                <button
                  v-for="tag in tags"
                  :key="tag"
                  type="button"
                  class="etag"
                  :class="[toneOf(tag), { focus: focusTag === tag }]"
                  :title="SCOPE_META.turn.hint"
                  @click="emit('focus-tag', tag, turn.index)"
                ><i />{{ tag }}</button>
                <span v-if="!tags.length" class="etag-none">无</span>
              </div>
              <div class="jtagrow">
                <span class="jscope">会话级</span>
                <template v-if="sessionTags.length">
                  <span
                    v-for="tag in sessionTags"
                    :key="tag"
                    class="etag"
                    :class="toneOf(tag)"
                    :title="SCOPE_META.session.hint"
                  ><i />{{ tag }}</span>
                  <span class="etag-note">未拆分到轮次</span>
                </template>
                <span v-else class="etag-none">无</span>
              </div>
            </div>
            <div class="dsub">轮次级计入本轮判定；会话级按整通会话判定，只作为本轮判定上下文，不参与本轮 pass。</div>
            <div v-if="focusTag && (tags.includes(focusTag) || sessionTags.includes(focusTag))" class="dnote">
              <TraceIcon name="info" :size="11" />
              <span>
                已定位到「{{ focusTag }}」：{{ SCOPE_META[sessionTags.includes(focusTag) ? 'session' : 'turn'].hint }}。
              </span>
            </div>
          </section>

          <section v-if="turn.score !== null || turn.dimensions.length" class="dsec">
            <h5>裁决返回校验 <span class="sp" /><span class="cnt">schema</span></h5>
            <div class="dkv">
              <div class="kv"><span class="k">维度合计</span><span class="v">{{ drawerDims.length ? `${fmtScore(dimensionTotal)} / 100` : '无四维分' }}</span></div>
              <div class="kv"><span class="k">合计 = {{ model.gradingMode === 'ANSWER_CONTRACT' ? '质量分' : '综合分' }}</span><span class="v">{{ dimsCompareScore !== null && Math.abs(dimensionTotal - dimsCompareScore) < 0.05 ? '是' : (drawerDims.length ? '否' : '无四维分') }}</span></div>
              <template v-if="model.gradingMode === 'ANSWER_CONTRACT'">
                <div class="kv"><span class="k">质量分</span><span class="v">{{ turn.score === null ? '未提供轮次级质量分' : fmtScore(turn.score) }}<em>仅参考，不参与 pass（后端没有把它和阈值比较）</em></span></div>
                <div class="kv"><span class="k">按契约闸门 pass</span><span class="v">{{ detail?.pass_flag === true ? 'true' : detail?.pass_flag === false ? 'false' : '未记录' }}<em>执行级判定，不由质量分推出</em></span></div>
              </template>
              <template v-else>
                <div class="kv"><span class="k">按分数 pass</span><span class="v">{{ turn.score === null ? '未提供轮次分' : turn.score >= passLine ? 'true' : 'false' }}</span></div>
                <div class="kv"><span class="k">按硬门槛 pass</span><span class="v">{{ !turn.rules.length && !turn.contract ? '本轮无硬门槛' : turn.gateOk ? 'true' : 'false' }}</span></div>
              </template>
              <div class="kv"><span class="k">标签均在枚举内</span><span class="v">{{ tagsInEnum ? '是' : '否' }}</span></div>
            </div>
          </section>

          <section class="dsec">
            <h5>裁决记录 <span class="sp" /><span class="cnt">就地展开 · 全文见「证据 · 流程裁决」</span></h5>
            <div v-if="judgePrompt || judgeRaw" class="dr-payload">
              <div class="djitem">
                <button type="button" class="djrow" :aria-expanded="rowOpen('jprompt')" title="就地展开裁决 Prompt，不跳出「判定」" @click="toggleRow('jprompt')">
                  <TraceIcon name="scale" :size="11" />
                  <span>发送的裁决 Prompt</span>
                  <span class="sp" />
                  <b>text/plain</b>
                  <span class="go">›</span>
                </button>
                <div v-if="rowOpen('jprompt')" class="djbody">
                  <CopyBox title="裁决 Prompt" kind="text" :value="judgePrompt" :empty="'执行记录未保存裁决 Prompt'" :max-height="'32vh'" />
                </div>
              </div>
              <div class="djitem">
                <button type="button" class="djrow" :aria-expanded="rowOpen('jres')" title="就地展开裁判原始返回，不跳出「判定」" @click="toggleRow('jres')">
                  <TraceIcon name="code" :size="11" />
                  <span>裁判原始返回</span>
                  <span class="sp" />
                  <b>{{ judgeRaw ? '已记录' : '未记录' }}</b>
                  <span class="go">›</span>
                </button>
                <div v-if="rowOpen('jres')" class="djbody">
                  <CopyBox title="裁判返回" :value="judgeRaw" :empty="'执行记录未保存裁判原始返回'" :max-height="'32vh'" />
                </div>
              </div>
            </div>
            <div v-else class="dempty">执行记录既没有裁决 Prompt，也没有裁判原始返回。</div>
            <div class="dkv" style="margin-top: 8px">
              <div class="kv"><span class="k">裁判模型</span><span class="v">{{ judgeModel }}</span></div>
              <div class="kv"><span class="k">裁判 tokens</span><span class="v">{{ judgeTokens || '未记录' }}</span></div>
              <div class="kv"><span class="k">trace</span><span class="v">{{ traceUid || '未记录' }}</span></div>
            </div>
          </section>
        </template>

        <!-- ============================ 执行 ============================ -->
        <template v-else-if="section === 'exec'">
          <section class="dsec">
            <h5>执行性能 <span class="sp" /><span class="cnt">{{ fmtMs(turn.latency) }}</span></h5>
            <div class="dgrid">
              <div class="dstat"><div class="dl">首 Token</div><div class="dv">{{ fmtMs(turn.ttft) }}</div></div>
              <div class="dstat"><div class="dl">总时延</div><div class="dv">{{ fmtMs(turn.latency) }}</div></div>
              <div class="dstat"><div class="dl">调用步数</div><div class="dv">{{ turn.steps.length }} 步</div></div>
              <div class="dstat"><div class="dl">最慢一步</div><div class="dv">{{ slowest ? fmtMs(slowest.ms) : '—' }}</div></div>
            </div>
            <div class="dgrid">
              <div class="dstat"><div class="dl">输入 tokens</div><div class="dv">{{ turn.promptTokens }}</div></div>
              <div class="dstat"><div class="dl">输出 tokens</div><div class="dv">{{ turn.completionTokens }}</div></div>
              <div class="dstat"><div class="dl">合计 tokens</div><div class="dv">{{ turn.tokens }}</div></div>
              <div class="dstat"><div class="dl">工具种类</div><div class="dv">{{ toolCount }} 个</div></div>
            </div>
          </section>

          <section class="dsec">
            <h5>模型与参数 <span class="sp" /><span class="cnt">按落库字段</span></h5>
            <div class="dkv">
              <div class="kv"><span class="k">模型</span><span class="v">{{ agentModelLabel || '执行记录未保存' }}</span></div>
              <div class="kv"><span class="k">温度 / 上限</span><span class="v">{{ samplingLabel || '执行记录未保存' }}</span></div>
              <div class="kv"><span class="k">stream</span><span class="v">{{ streamLabel || '执行记录未保存' }}</span></div>
              <div class="kv"><span class="k">response format</span><span class="v">{{ responseFormatLabel || '执行记录未保存' }}</span></div>
              <div class="kv"><span class="k">输入来源</span><span class="v">{{ sourceLabel }}</span></div>
              <div class="kv"><span class="k">会话</span><span class="v">{{ turn.sessionId || '未记录' }}</span></div>
              <div class="kv"><span class="k">环境</span><span class="v">{{ envLabel }}</span></div>
              <div class="kv"><span class="k">判分模式</span><span class="v">{{ model.gradingMode === 'ANSWER_CONTRACT' ? 'ANSWER_CONTRACT · 契约模式' : model.gradingMode === 'RUBRIC' ? 'RUBRIC · 旧评分模式（历史数据）' : 'LEGACY · 规则 + LLM 裁判' }}</span></div>
            </div>
          </section>

          <section class="dsec">
            <h5>调用链 <span class="sp" /><span class="cnt">{{ turn.steps.length }} 步 · {{ toolCount }} 种工具 · {{ fmtMs(stepTotalMs) }}</span></h5>
            <ChainList :steps="turn.steps" :turn-index="turn.index" />
            <div v-if="stepWarnCount" class="dnote2">
              调用链有 {{ stepWarnCount }} 步异常（{{ warnStepNames }}）：在下方调用链中展开异常步骤即可看到原始入参 / 出参；异常步骤只做标记，不改变调用顺序。
            </div>
            <div class="dsub">
              按阶段分组、组内折叠；展开单步可看 arguments / result 原文（后端未拆分时给原始条目）并单独复制。阶段只做归类，不改变调用顺序。
            </div>
          </section>

          <section class="dsec">
            <h5>定位 <span class="sp" /><span class="cnt">trace 关联</span></h5>
            <div class="dkv">
              <div class="kv"><span class="k">trace</span><span class="v">{{ traceUid || '未记录' }}</span></div>
              <div class="kv"><span class="k">轮次</span><span class="v">T{{ turn.index }} / 共 {{ turns.length }} 轮</span></div>
              <div class="kv"><span class="k">最慢工具</span><span class="v">{{ slowest ? `${slowest.tool} · ${fmtMs(slowest.ms)}` : '—' }}</span></div>
              <div class="kv"><span class="k">用例</span><span class="v">{{ caseLabel }}</span></div>
            </div>
            <div class="dsub">完整请求 / 响应与调用参数见「证据」；跨轮 trace 可在页面级调用链中聚合查看。</div>
          </section>
        </template>

        <!-- ============================ 证据 ============================ -->
        <template v-else-if="section === 'evid'">
          <div class="dtabs">
            <template v-for="(item, index) in evidenceTabs" :key="item.key">
              <span v-if="index > 0 && evidenceTabs[index - 1].group !== item.group" class="tbsep" aria-hidden="true" />
              <button type="button" :class="{ on: evidTab === item.key }" :title="item.label" @click="emit('evid-tab', item.key)">
                {{ item.label }}
              </button>
            </template>
          </div>

          <div v-if="evidTab === 'req'">
            <div class="mquote">
              <div class="ml"><TraceIcon name="user" :size="10" />输入原文</div>
              <div class="mt">{{ turn.query || '（本轮没有提问记录）' }}</div>
              <div v-if="turn.variables.length" class="mvars">
                <span v-for="([key, value], index) in turn.variables" :key="index">{{ key }}={{ value }}</span>
              </div>
            </div>
            <CopyBox
              title="请求头"
              kind="headers"
              :value="turn.request.headers"
              :empty="'执行记录未保存请求头'"
            />
            <CopyBox
              title="请求体"
              :value="turn.request.body"
              :empty="'执行记录未保存请求体（request_payload.body 为空）'"
            />
            <div class="dnote">
              <TraceIcon name="info" :size="11" />
              <span>
                请求地址 {{ turn.request.method ? `${turn.request.method} ` : '' }}{{ turn.request.url || '（未记录 URL）' }}；敏感请求头在落库时已被脱敏。
                工具调用与判定过程分别见「Agent 调用链」「流程裁决」。
              </span>
            </div>
          </div>

          <div v-else-if="evidTab === 'res'">
            <div class="dkv" style="margin-bottom: 8px">
              <div class="kv">
                <span class="k">状态码</span>
                <span class="v">{{ turn.responseStatus ?? '未保存' }}</span>
              </div>
              <div class="kv">
                <span class="k">响应形态</span>
                <span class="v">{{ turn.responseMode === 'sse' ? `流式 · ${turn.responseFrames ?? '—'} 帧` : '非流式 JSON' }}</span>
              </div>
            </div>
            <CopyBox
              title="响应头"
              kind="headers"
              :value="turn.responseHeaders"
              :empty="'本执行未保存响应头（历史执行记录；新执行已随报文落库并脱敏）'"
            />
            <CopyBox
              title="响应体"
              :value="turn.responseBody"
              :empty="'执行记录未保存响应体（response_raw 为空）'"
            />
            <div class="dnote">
              <TraceIcon name="info" :size="11" />
              <span v-if="turn.responseMode === 'sse'">
                流式响应：共 {{ turn.responseFrames ?? '—' }} 帧，这里展示的是聚合后的内容。
              </span>
              <span v-else>
                响应体来自执行记录；<code>answer</code> 字段保留原始标记，界面按行渲染，不修改原文。
              </span>
            </div>
          </div>

          <div v-else-if="evidTab === 'follow'">
            <section class="dsec">
              <h5>本轮追问链 <span class="sp" /><span class="cnt">{{ previousTurn ? `承接 T${previousTurn.index}` : '首轮' }}</span></h5>
              <div class="dchain">
                <div v-if="previousTurn" class="ln">
                  <span class="k">上一轮</span>
                  <span class="v">{{ previousTurn.query }}<em>T{{ previousTurn.index }} · {{ previousTurn.intent || '未记录意图' }}</em></span>
                </div>
                <div class="ln">
                  <span class="k">本轮</span>
                  <span class="v">{{ turn.query }}<em>T{{ turn.index }} · {{ turn.intent || '未记录意图' }}</em></span>
                </div>
                <div class="ln">
                  <span class="k">输入来源</span>
                  <span class="v">{{ sourceLabel }}<em>多轮指代由 Agent 结合会话历史解析</em></span>
                </div>
              </div>
              <div class="dsub">追问链只记录本轮相对上一轮的问题与来源；原始 messages 见「请求」。</div>
            </section>

            <section v-if="selfRecord || !executionPassed" class="dsec">
              <h5>追问自检 <span class="sp" /><span class="cnt">{{ turn.selfCheck ? '已触发' : selfRecord ? (selfCheckTurn ? '其他轮触发' : '执行级记录') : selfSkipReason ? '未触发 · SKIPPED' : '未触发' }}</span></h5>
              <template v-if="turn.selfCheck && selfRecord">
                <CopyBox title="实际发送 · 自检 Prompt" kind="text" :value="selfRecord.prompt" :empty="'自检记录未保存 Prompt'" :max-height="'28vh'" />
                <CopyBox title="自检返回" :value="selfRecord.response_raw" :empty="'自检记录未保存原始返回'" :max-height="'28vh'" />
                <div class="dsub">
                  自检是评测器追加的一次诊断调用，问题是系统生成的，不会额外发送一条用户消息；结论不改变本轮 pass。
                </div>
              </template>
              <div v-else-if="selfRecord && selfCheckTurn" class="dempty">
                <TraceIcon name="refresh" :size="14" />
                <b>本轮没有追问自检</b>
                <p>自检只在轮次未通过后自动发起，当前会话由 T{{ selfCheckTurn.index }} 触发 1 次。</p>
                <button type="button" class="ev-chip" @click="emit('switch-turn', selfCheckTurn.index)">
                  <TraceIcon name="refresh" :size="10" />
                  查看 T{{ selfCheckTurn.index }} 的追问内容
                  <span class="go">›</span>
                </button>
              </div>
              <div v-else-if="selfRecord && !selfCheckTurn" class="dempty">
                <TraceIcon name="refresh" :size="14" />
                <b>本次执行有 1 条追问自检</b>
                <p>执行记录保存了这条自检，但没有触发轮次（self_check.turn_index 为空）；自检内容按执行级归档，不推测属于哪一轮。</p>
                <button type="button" class="ev-chip" @click="emit('section', 'context')">
                  <TraceIcon name="refresh" :size="10" />
                  查看「派生与上下文」中的自检
                  <span class="go">›</span>
                </button>
              </div>
              <div v-else class="dempty">
                {{ selfSkipReason ? `本次执行未触发追问自检：${selfSkipReason}（self_check = SKIPPED）。` : '本次执行没有追问自检记录。' }}
              </div>
            </section>
          </div>

          <div v-else-if="evidTab === 'clarify'">
            <section class="dsec">
              <h5>反问记录 <span class="sp" /><span class="cnt">{{ clarifyState }}</span></h5>
              <div v-if="clarifyTag" class="dverdict warn">
                <TraceIcon name="alert" :size="12" />
                <div>
                  <b>应反问而未反问</b>
                  <span>
                    会话级错误归因「未主动反问」已命中：评测器认为本轮信息不足或口径不明，Agent 直接作答没有先澄清。
                    具体建议反问内容后端未落库，界面不补写。
                  </span>
                </div>
              </div>
              <div v-else-if="isClarifyTurn" class="dverdict ok">
                <TraceIcon name="chat" :size="12" />
                <div>
                  <b>本轮 Agent 主动澄清</b>
                  <span>
                    本轮意图为 CLARIFY，Agent 先向用户确认口径；反问内容直接取自本轮实际回复，后端没有单独的反问字段。
                  </span>
                </div>
              </div>
              <div v-else class="dempty">
                <TraceIcon name="chat" :size="14" />
                <b>本轮未发生反问</b>
                <p>没有命中「未主动反问」，也没有单独的反问记录字段。</p>
              </div>
            </section>
            <section class="dsec">
              <h5>记录结构 <span class="sp" /><span class="cnt">能力预留</span></h5>
              <div class="dchain">
                <div class="ln"><span class="k">触发条件</span><span class="v">信息不足、口径不明或存在多种合理解释<em>由 Agent 或评测器判定</em></span></div>
                <div class="ln"><span class="k">反问内容</span><span class="v">面向用户的一句澄清问题<em>计入会话消息，不单独计为轮次</em></span></div>
                <div class="ln">
                  <span class="k">用户回复</span>
                  <span class="v">
                    {{ clarifyTag ? '无 · 本轮未发起反问，没有对应的用户回复' : '无 · 本轮不是反问轮' }}
                    <em>反问轮才作为下一轮上下文继续执行</em>
                  </span>
                </div>
                <div class="ln"><span class="k">判定影响</span><span class="v">是否及时、是否问到关键口径<em>写入错误归因：未主动反问</em></span></div>
              </div>
            </section>
          </div>

          <div v-else-if="evidTab === 'chain'">
            <div class="mhd-line">
              <span>Agent 调用链</span>
              <span class="badge">按调用顺序</span>
              <span class="sp" />
              <span class="badge">{{ turn.steps.length }} 步 · {{ fmtMs(stepTotalMs) }}</span>
            </div>
            <div class="dgrid">
              <div class="dstat"><div class="dl">步数</div><div class="dv">{{ turn.steps.length }}</div></div>
              <div class="dstat"><div class="dl">工具种类</div><div class="dv">{{ toolCount }}</div></div>
              <div class="dstat" :class="{ warn: stepWarnCount > 0 }"><div class="dl">异常步骤</div><div class="dv">{{ stepWarnCount }}</div></div>
              <div class="dstat"><div class="dl">总耗时</div><div class="dv">{{ fmtMs(stepTotalMs) }}</div></div>
            </div>
            <ChainList :steps="turn.steps" :turn-index="turn.index" compact />
            <div class="dsub">
              每条记录可展开 tool call 的 arguments / result 原始报文并单独复制；分组折叠避免几十步撑破面板。
            </div>
          </div>

          <div v-else-if="evidTab === 'sql'">
            <CopyBox
              title="生成的 SQL"
              :value="turn.sql"
              note="response_raw"
              kind="text"
              :empty="'本轮未生成 SQL'"
              :max-height="'40vh'"
            />
            <div class="dnote">
              <TraceIcon name="info" :size="11" />
              <span>SQL 来自本轮响应体，只用于核对取数口径，不作为回复正文。</span>
            </div>
          </div>

          <div v-else-if="evidTab === 'judge'">
            <div class="mhd-line">
              <span>流程裁决</span>
              <span class="badge">输入 → 裁判 → 返回 → 校验</span>
              <span class="sp" />
              <span class="badge">
                {{ judgeModel }}<template v-if="judgeTemperature !== null"> · temp {{ judgeTemperature }}</template>
              </span>
              <button type="button" class="mcopy" title="复制流程裁决全文" @click="copyJudgeFlow">
                <TraceIcon name="copy" :size="10" />
                复制
              </button>
            </div>
            <section class="dsec">
              <div class="dchain">
                <div class="ln">
                  <span class="k">输入</span>
                  <span class="v">
                    <template v-if="model.gradingMode === 'ANSWER_CONTRACT'">
                      用户问题 + 候选回答 + 白盒取数明细 + 期望答案<em>契约闸门由后端规则计算，不交给模型判断；模型只给质量分</em>
                    </template>
                    <template v-else>用户问题 + 候选回答 + 白盒取数明细 + 期望答案<em>取数明细与期望答案由系统先算好再拼入，不交给模型自行判断</em></template>
                  </span>
                </div>
                <div class="ln">
                  <span class="k">调用</span>
                  <span class="v">{{ judgeModel }}<em>裁判 tokens {{ judgeTokens || '未记录' }}</em></span>
                </div>
                <div class="ln">
                  <span class="k">返回</span>
                  <span class="v">
                    <template v-if="model.gradingMode === 'ANSWER_CONTRACT'">
                      质量分 {{ fmtScore(turn.score) }} · 错误归因 {{ tags.length + sessionTags.length }} 条<em>{{ judgeConclusion }}；pass 不由裁判给出，按契约闸门计算</em>
                    </template>
                    <template v-else>
                      pass = {{ turn.pass }} · 综合分 {{ fmtScore(turn.score) }} · 错误归因 {{ tags.length + sessionTags.length }} 条<em>{{ judgeConclusion }}</em>
                    </template>
                  </span>
                </div>
                <div class="ln">
                  <span class="k">校验</span>
                  <span class="v">
                    <template v-if="model.gradingMode === 'ANSWER_CONTRACT'">
                      本轮 pass {{ turn.verdict === 'unknown' ? '—' : turn.pass }} · 质量分 {{ fmtScore(turn.score) }}（仅参考）
                      <em>契约模式的 pass 只看四项闸门与终止状态，质量分高低不改变 pass</em>
                    </template>
                    <template v-else>
                      按分数 pass {{ turn.score === null ? '未提供轮次分' : turn.score >= passLine }} / 按硬门槛 pass {{ turn.gateOk }}
                      <em v-if="turn.score === null">本轮没有轮次级综合分，pass 只按已落库的硬门槛判定</em>
                      <em v-else>pass = 硬门槛全过 且综合分 ≥ {{ passLine }}，两者不一致时以硬门槛为准</em>
                    </template>
                  </span>
                </div>
              </div>
            </section>

            <section class="dsec">
              <h5>原始记录 <span class="sp" /><span class="cnt">点击就地展开</span></h5>
              <div v-if="judgePrompt || judgeRaw" class="dr-payload">
                <div class="djitem">
                  <button type="button" class="djrow" :aria-expanded="rowOpen('ejprompt')" @click="toggleRow('ejprompt')">
                    <TraceIcon name="scale" :size="11" />
                    <span>发送的裁决 Prompt</span>
                    <span class="sp" />
                    <b>text/plain</b>
                    <span class="go">›</span>
                  </button>
                  <div v-if="rowOpen('ejprompt')" class="djbody">
                    <CopyBox title="裁决 Prompt" kind="text" :value="judgePrompt" :empty="'未保存'" :max-height="'32vh'" />
                  </div>
                </div>
                <div class="djitem">
                  <button type="button" class="djrow" :aria-expanded="rowOpen('ejres')" @click="toggleRow('ejres')">
                    <TraceIcon name="code" :size="11" />
                    <span>裁判原始返回</span>
                    <span class="sp" />
                    <b>{{ judgeRaw ? '已记录' : '未记录' }}</b>
                    <span class="go">›</span>
                  </button>
                  <div v-if="rowOpen('ejres')" class="djbody">
                    <CopyBox title="裁判返回" :value="judgeRaw" :empty="'未保存'" :max-height="'32vh'" />
                  </div>
                </div>
              </div>
              <div v-else class="dempty">执行记录没有裁决 Prompt 与裁判原始返回。</div>
              <div class="dsub">trace {{ traceUid || '未记录' }}；「判定」段的裁决记录只做就地预览，这里保存完整原始报文。</div>
            </section>
          </div>

          <div class="dsub" style="margin-top: 10px">
            证据分三组：<b>对话内容</b>（请求 / 响应 / 追问 / 反问）、<b>执行证据</b>（Agent 调用链 / SQL）、<b>判定材料</b>（流程裁决）；
            请求与响应均含报文头。分析视图见「执行」段，页签随本轮实际发生的动作增减。
          </div>
        </template>

        <!-- ======================= 派生与上下文 ======================= -->
        <template v-else-if="section === 'context'">
          <section v-if="selfRecord || !executionPassed" class="dsec">
            <h5>
              追问自检
              <span class="sp" />
              <span class="cnt">{{ selfRecord ? (turn.selfCheck ? `已触发 · 派生轮次 S1` : selfCheckTurn ? `由 T${selfCheckTurn.index} 触发 · 本轮未触发` : '执行级记录 · 未记录触发轮次') : selfSkipReason ? '未触发 · SKIPPED' : '未记录' }}</span>
            </h5>
            <template v-if="selfRecord">
              <div class="dverdict" :class="selfRecord.status === 'DONE' ? 'warn' : ''">
                <TraceIcon :name="selfRecord.status === 'DONE' ? 'refresh' : 'alert'" :size="12" />
                <div>
                  <b>{{ turn.selfCheck ? '自检结论' : selfCheckTurn ? `${selfTriggerLabel()} 的自检` : '自检结论（执行级）' }}</b>
                  <span>{{ selfRecord.conclusion || selfRecord.skip_reason || selfRecord.error || '自检未产生结论' }}</span>
                </div>
              </div>
              <div class="dgrid">
                <div class="dstat"><div class="dl">状态</div><div class="dv">{{ statusLabelOf(selfRecord.status) }}</div></div>
                <div class="dstat"><div class="dl">触发轮次</div><div class="dv">{{ selfTriggerValue }}</div></div>
                <div class="dstat"><div class="dl">时延</div><div class="dv">{{ fmtMs(selfRecord.latency_ms) }}</div></div>
                <div class="dstat"><div class="dl">参与本轮判定</div><div class="dv">否</div></div>
              </div>
              <div class="dkv" style="margin-top: 8px">
                <div class="kv"><span class="k">触发原因</span><span class="v">{{ selfRecord.trigger || '未记录' }}</span></div>
                <div class="kv"><span class="k">模板</span><span class="v">{{ selfRecord.template_name || '未记录模板' }}{{ selfRecord.template_version ? ` v${selfRecord.template_version}` : '' }}</span></div>
                <div class="kv"><span class="k">会话</span><span class="v">{{ selfRecord.session_id || turn.sessionId || '未记录' }}</span></div>
                <div class="kv"><span class="k">发生时间</span><span class="v">{{ selfRecord.performed_at ? selfRecord.performed_at.replace('T', ' ').slice(0, 19) : '未记录' }}</span></div>
              </div>
              <div v-if="!selfCheckTurn" class="dsub">
                执行记录的 self_check 未保存触发轮次（turn_index 为空）；这里按<b>执行级</b>展示，不推测具体轮次。
              </div>
              <button
                v-if="selfCheckTurn && selfCheckTurn.index !== turn.index"
                type="button"
                class="ev-chip wide"
                style="margin-top: 8px"
                @click="emit('switch-turn', selfCheckTurn.index)"
              >
                <TraceIcon name="refresh" :size="10" />
                <span>在面板内切到 T{{ selfCheckTurn.index }} 的追问自检</span>
                <span class="sp" />
                <span class="go">›</span>
              </button>
              <div class="dsub">
                派生轮次由失败自动发起，用于复核原因，不改变本轮 pass / {{ model.gradingMode === 'ANSWER_CONTRACT' ? '质量分' : '综合分' }}；只有 DONE / SKIPPED / ERROR 三态。
              </div>
            </template>
            <div v-else class="dempty">
              {{
                selfSkipReason
                  ? `本次执行未触发追问自检：${selfSkipReason}（self_check = SKIPPED）。`
                  : '本次执行没有追问自检记录（self_check 为空）。'
              }}
            </div>
          </section>

          <section v-if="selfRecord && (turn.selfCheck || !selfCheckTurn)" class="dsec">
            <h5>
              实际发送 · 自检 Prompt
              <span class="sp" />
              <span class="cnt">{{ (selfRecord.prompt || '').length }} 字 · {{ selfCheckTurn ? `T${selfCheckTurn.index}` : '执行级' }}</span>
            </h5>
            <CopyBox
              title="自检 Prompt"
              kind="text"
              :value="selfRecord.prompt"
              :empty="'自检记录未保存 Prompt'"
              :max-height="'38vh'"
            />
            <div class="dsub">
              这是本轮实际发给 Agent 的完整指令：模板渲染后整体发出，会话历史按原样带上；追问没有单独再发一条用户消息。
            </div>
            <button type="button" class="ev-chip wide" style="margin-top: 8px" @click="emit('page', 'selfcheck')">
              <TraceIcon name="file" :size="10" />
              <span>打开完整自检页</span>
              <span class="sp" />
              <span class="go">›</span>
            </button>
          </section>

          <section class="dsec">
            <h5>会话级判定上下文 <span class="sp" /><span class="cnt">不参与本轮 pass</span></h5>
            <div class="dgrid">
              <div class="dstat">
                <div class="dl">{{ model.gradingMode === 'ANSWER_CONTRACT' ? '质量分参考' : '执行级综合分' }}</div>
                <div class="dv">{{ fmtScore(model.judges.length ? model.judges[0].total_score : sessionAvg) }}<small v-if="(model.judges.length ? model.judges[0].total_score : sessionAvg) !== null"> / 100</small></div>
              </div>
              <div class="dstat" :class="{ warn: model.session.passCount < turns.length }">
                <div class="dl">通过</div>
                <div class="dv">{{ model.session.passCount }} / {{ turns.length }}</div>
              </div>
              <div class="dstat">
                <div class="dl">最低轮次</div>
                <div class="dv">{{ model.session.lowest ? `T${model.session.lowest.index}${model.session.lowest.score !== null ? ` · ${fmtScore(model.session.lowest.score)}` : ''}` : '—' }}</div>
              </div>
              <div class="dstat" :class="{ warn: model.session.tagCount > 0 }">
                <div class="dl">归因总数</div>
                <div class="dv">{{ model.session.tagCount }} 条</div>
              </div>
            </div>
            <div class="dkv" style="margin-top: 8px">
              <div class="kv"><span class="k">执行级结论</span><span class="v">{{ sessionVerdictLabel }}</span></div>
              <div class="kv"><span class="k">{{ model.gradingMode === 'ANSWER_CONTRACT' ? '质量分合计' : '总分' }}</span><span class="v">{{ sessionScoreTotal || '—' }}</span></div>
              <div class="kv"><span class="k">四维均分</span><span class="v">{{ sessionDimText || '—' }}</span></div>
              <div class="kv"><span class="k">会话 ID</span><span class="v">{{ model.session.sessionIds.length ? model.session.sessionIds.join(' / ') : '未记录' }}</span></div>
              <div class="kv"><span class="k">会话一致性</span><span class="v">{{ model.session.consistentSession ? '同一会话' : '多会话（逐轮新会话）' }}</span></div>
              <div class="kv"><span class="k">{{ model.gradingMode === 'ANSWER_CONTRACT' ? '阈值（不参与契约判定）' : 'pass 阈值' }}</span><span class="v">{{ passLine }} · {{ passLineSource }}</span></div>
            </div>
            <div v-if="sessionTagsAll.length" class="jtagrow" style="margin-top: 8px">
              <span class="jscope" :title="SCOPE_META.session.hint">会话级归因</span>
              <span
                v-for="tag in sessionTagsAll"
                :key="tag"
                class="etag"
                :class="toneOf(tag)"
                :title="SCOPE_META.session.hint"
              ><i />{{ tag }}</span>
              <span class="etag-note">未拆分到轮次</span>
            </div>
            <div class="dsub">会话级归因只在「判定」段标注；这里给出会话汇总与最低轮次，帮助解释本轮所处位置，不重复计入本轮判定。<template
                v-if="turns.length > 1 && !model.session.lowest"
              > 本次执行未落库轮次级分数，轮次高低无法比较，「最低轮次」显示 —。</template></div>
          </section>

          <section class="dsec">
            <h5>执行快照 <span class="sp" /><span class="cnt">只读</span></h5>
            <div class="dkv">
              <div class="kv"><span class="k">执行</span><span class="v">{{ executionLabel }}</span></div>
              <div class="kv"><span class="k">用例</span><span class="v">{{ caseLabel }}</span></div>
              <div class="kv"><span class="k">用例版本</span><span class="v">v{{ detail?.case_version ?? '—' }}</span></div>
              <div class="kv"><span class="k">尝试次数</span><span class="v">#{{ detail?.attempt_no ?? '—' }}</span></div>
              <div class="kv"><span class="k">环境</span><span class="v">{{ envLabel }}</span></div>
              <div class="kv"><span class="k">最大轮次</span><span class="v">{{ detail?.max_turns ?? '—' }}</span></div>
              <div class="kv"><span class="k">开始时间</span><span class="v">{{ detail?.started_at ? detail.started_at.replace('T', ' ').slice(0, 19) : '—' }}</span></div>
              <div class="kv"><span class="k">结束时间</span><span class="v">{{ detail?.finished_at ? detail.finished_at.replace('T', ' ').slice(0, 19) : '—' }}</span></div>
              <div class="kv"><span class="k">模型</span><span class="v">{{ agentModelLabel || '执行记录未保存' }}</span></div>
              <div class="kv"><span class="k">裁判模型</span><span class="v">{{ judgeModel }}</span></div>
              <div class="kv"><span class="k">{{ model.gradingMode === 'ANSWER_CONTRACT' ? '阈值（不参与契约判定）' : 'pass 阈值' }}</span><span class="v">{{ passLine }} · {{ passLineSource }}</span></div>
            </div>
          </section>

          <section v-if="model.sessionVariables.length" class="dsec">
            <h5>请求变量 <span class="sp" /><span class="cnt">用例快照</span></h5>
            <div class="dchips">
              <span v-for="([key, value], index) in model.sessionVariables" :key="index"><b>{{ key }}</b> {{ value }}</span>
            </div>
          </section>
        </template>
      </div>

      <footer class="dr-foot">
        <span class="trace">trace {{ traceUid || '未记录' }}</span>
        <span v-if="turn">· T{{ turn.index }} / 共 {{ turns.length }} 轮</span>
        <span class="sp" />
        <span>只读 · 已脱敏</span>
      </footer>
    </aside>
  </Teleport>
</template>

<style scoped>
.dimmer {
  position: fixed;
  inset: 0;
  background: rgba(17, 24, 39, 0.28);
  backdrop-filter: blur(1px);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.22s ease;
  z-index: 60;
}
.dimmer.on { opacity: 1; pointer-events: auto; }
.drawer {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: min(860px, 94vw);
  max-width: 100vw;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-left: 1px solid #e8e8ed;
  box-shadow: -24px 0 60px -30px rgba(17, 24, 39, 0.35);
  transform: translateX(102%);
  transition: transform 0.26s cubic-bezier(0.32, 0.72, 0, 1);
  z-index: 61;
  min-width: 0;
}
.drawer.on { transform: translateX(0); }

.dr-head {
  flex: none;
  padding: 13px 18px 0;
  border-bottom: 1px solid #f0f0f4;
  background: linear-gradient(180deg, #fcfcff, #fff 72%);
  min-width: 0;
}
.dr-top { display: flex; align-items: flex-start; gap: 8px; min-width: 0; }
.dr-id {
  flex: none;
  font-family: Consolas, monospace;
  font-size: 11px;
  font-weight: 700;
  color: #4f46e5;
  background: #eef2ff;
  border-radius: 7px;
  padding: 3px 7px;
  margin-top: 1px;
}
.stat-pill {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 9.5px;
  border-radius: 999px;
  padding: 2px 8px;
  border: 1px solid #e8e8ed;
  background: #fff;
  color: #6b7280;
  white-space: nowrap;
  margin-top: 1px;
}
.stat-pill.pass { background: #ecfdf5; border-color: #a7f3d0; color: #047857; }
.stat-pill.warn { background: #fffbeb; border-color: #fde68a; color: #b45309; }
.stat-pill.fail { background: #fff1f2; border-color: #fecdd3; color: #be123c; }
.stat-pill.unknown { background: #f8fafc; border-color: #e2e8f0; color: #64748b; }
.dr-q {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.55;
  color: #111827;
  word-break: break-word;
  overflow-wrap: anywhere;
}
.dr-close {
  flex: none;
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border: 1px solid #e8e8ed;
  border-radius: 7px;
  background: #fff;
  color: #6b7280;
  cursor: pointer;
}
.dr-close:hover { border-color: #fecdd3; color: #be123c; background: #fff1f2; }

.dr-meta {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
  font-size: 9.5px;
  color: #9ca3af;
  margin-top: 6px;
  font-family: Consolas, monospace;
}
.dr-meta .dot { color: #d4d4d8; }
.dr-meta b { font-weight: 600; color: #374151; }
.dr-meta b.low { color: #b45309; }

.dr-turns { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; margin-top: 9px; }
.dr-turns .lbl {
  font-size: 8.5px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #b0b6c1;
  margin-right: 2px;
}
.dr-turns button {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  border: 1px solid #e8e8ed;
  background: #fff;
  border-radius: 7px;
  padding: 2px 7px;
  cursor: pointer;
  color: #6b7280;
}
.dr-turns button b { font-family: Consolas, monospace; font-size: 9.5px; font-weight: 600; }
.dr-turns button .s { font-size: 8.5px; color: #9ca3af; }
.dr-turns button:hover { border-color: #c7d2fe; }
.dr-turns button.pass { background: #ecfdf5; border-color: #a7f3d0; color: #047857; }
.dr-turns button.warn { background: #fffbeb; border-color: #fde68a; color: #b45309; }
.dr-turns button.fail { background: #fff1f2; border-color: #fecdd3; color: #be123c; }
.dr-turns button.on { box-shadow: 0 0 0 2px rgba(79, 70, 229, 0.16); border-color: #818cf8; }

.dr-seg {
  display: flex;
  align-items: stretch;
  gap: 2px;
  margin-top: 10px;
  overflow-x: auto;
  scrollbar-width: none;
}
.dr-seg::-webkit-scrollbar { display: none; }
.dr-seg button {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 12px 8px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: none;
  font-size: 11px;
  color: #6b7280;
  cursor: pointer;
  white-space: nowrap;
}
.dr-seg button:hover { color: #4f46e5; }
.dr-seg button.on { color: #4f46e5; border-bottom-color: #4f46e5; font-weight: 600; }

.dr-body {
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 14px 18px 22px;
  overscroll-behavior: contain;
}
.dr-body::-webkit-scrollbar { width: 9px; }
.dr-body::-webkit-scrollbar-thumb { background: #e1e4eb; border-radius: 999px; border: 2px solid #fff; }
.dr-body::-webkit-scrollbar-thumb:hover { background: #cdd3dd; }

.dsec { margin-bottom: 18px; min-width: 0; }
.dsec h5 {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 7px;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.09em;
  text-transform: uppercase;
  color: #9ca3af;
  flex-wrap: wrap;
}
.dsec h5 .sp { flex: 1; }
.dsec h5 .cnt { letter-spacing: 0; text-transform: none; font-weight: 500; font-family: Consolas, monospace; font-size: 8.5px; color: #b0b6c1; }
.dsec h5 .dsrc {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  letter-spacing: 0;
  text-transform: none;
  font-weight: 500;
  font-size: 8.5px;
  color: #4f46e5;
  background: #eef2ff;
  border-radius: 5px;
  padding: 1px 5px;
  font-family: Consolas, monospace;
}
.dsub { font-size: 9.5px; line-height: 1.7; color: #9ca3af; margin-top: 6px; }
.dsub code { font-family: Consolas, monospace; font-size: 9px; color: #6b7280; background: #f4f4f7; border-radius: 4px; padding: 0 3px; }

.dgrid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; }
.dgrid + .dgrid { margin-top: 6px; }
.dstat { border: 1px solid #eceef3; background: #fbfbfd; border-radius: 9px; padding: 6px 8px; min-width: 0; }
.dstat.warn { background: #fffdf5; border-color: #fde68a; }
.dstat .dl { font-size: 8.5px; color: #9ca3af; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dstat .dv {
  font-family: Consolas, monospace;
  font-size: 11.5px;
  font-weight: 600;
  color: #374151;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dstat.warn .dv { color: #b45309; }
.dstat .dv small { font-size: 8.5px; font-weight: 400; color: #9ca3af; }

.dquicks { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 7px; }
.dquicks button,
.ev-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 1px solid #e8e8ed;
  background: #fff;
  border-radius: 8px;
  padding: 5px 9px;
  font-size: 10px;
  color: #6b7280;
  cursor: pointer;
  min-width: 0;
}
.dquicks button { flex: 1 1 180px; }
.dquicks button:hover,
.ev-chip:hover { border-color: #c7d2fe; background: #f8f9ff; }
.dquicks button span { color: #374151; font-weight: 500; }
.dquicks button em { font-style: normal; font-size: 8.5px; color: #b0b6c1; margin-left: auto; }
.dquicks .go,
.ev-chip .go { color: #c3c9d4; }
.ev-chip.wide { width: 100%; justify-content: flex-start; }
.ev-chip .sp { flex: 1; }

.dkv { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 18px; }
.dkv.one { grid-template-columns: 1fr; }
.kv {
  display: flex;
  gap: 8px;
  align-items: baseline;
  padding: 4px 0;
  border-bottom: 1px dashed #f0f0f4;
  font-size: 10px;
  min-width: 0;
}
.kv:last-child { border-bottom: 0; }
.kv .k { color: #9ca3af; font-size: 9.5px; flex: none; min-width: 58px; }
.kv .v {
  color: #374151;
  font-family: Consolas, monospace;
  font-size: 9.5px;
  min-width: 0;
  word-break: break-all;
  overflow-wrap: anywhere;
}
.kv .v b { font-weight: 600; }

.dchips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 7px; }
.dchips span {
  font-family: Consolas, monospace;
  font-size: 9px;
  background: #fbfbfd;
  border: 1px solid #e8e8ed;
  border-radius: 5px;
  padding: 2px 6px;
  color: #6b7280;
  max-width: 100%;
  word-break: break-all;
}
.dchips span b { font-weight: 500; color: #374151; }

.dexpect {
  border: 1px solid #eceef3;
  background: #fcfcfe;
  border-radius: 9px;
  padding: 9px 11px;
  font-size: 11px;
  line-height: 1.85;
  color: #374151;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}

/* 判定页首屏：一张「LLM 裁决卡」把结论、分数、期望基准收在一起，最终结果一眼可读。
   结论色只用于状态胶囊与极淡的卡头底色，分数保持中性，不与结论抢焦点。 */
.djudgecard {
  border: 1px solid #e3e6f5;
  border-left: 3px solid #6366f1;
  background: #fff;
  border-radius: 4px 10px 10px 4px;
  overflow: hidden;
}
.djv-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 12px;
  background: #f8f9ff;
  border-bottom: 1px solid #e9ecfb;
}
.djudgecard.jv-pass .djv-head { background: #f7fdfa; border-bottom-color: #e4f6ec; }
.djudgecard.jv-warn .djv-head { background: #fffdf5; border-bottom-color: #fdf2d6; }
.djudgecard.jv-fail .djv-head { background: #fff9f9; border-bottom-color: #fdecec; }
.djudgecard.jv-unknown .djv-head { background: #f8f9fb; border-bottom-color: #eef0f4; }
.djv-ic { display: inline-flex; color: #4f46e5; }
.djv-title { font-size: 11px; font-weight: 600; color: #312e81; letter-spacing: 0.01em; }
.djv-model { font-family: Consolas, monospace; font-size: 9px; color: #a3a8bd; }
.djv-head .sp { flex: 1; }
.djv-score { display: inline-flex; align-items: baseline; gap: 3px; }
.djv-score b { font-family: Consolas, monospace; font-size: 19px; font-weight: 700; line-height: 1; letter-spacing: -0.02em; color: #374151; }
.djv-score em { font-style: normal; font-family: Consolas, monospace; font-size: 9px; color: #9ca3af; }
.djv-score i { font-style: normal; font-size: 8.5px; color: #9ca3af; margin-left: 3px; }
.djv-body { padding: 11px 13px 10px; }
.djv-body p {
  margin: 0;
  font-size: 12px;
  line-height: 1.9;
  color: #374151;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}
.djv-body p.muted { font-size: 11px; color: #9ca3af; }
/* 中间轮常拿到会话级结论：用一枚小标签先说清来源，避免读者把结论当成这一轮的判定 */
.djv-src {
  display: inline-flex;
  align-items: center;
  margin-bottom: 6px;
  padding: 1px 6px;
  border: 1px solid #fde68a;
  border-radius: 4px;
  background: #fffbeb;
  font-size: 8.5px;
  line-height: 1.6;
  color: #b45309;
}
.djv-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 3px 12px;
  margin-top: 8px;
  font-family: Consolas, monospace;
  font-size: 9px;
  color: #9ca3af;
}
.djv-meta .flag { color: #8d93a3; }
.djv-basis { border-top: 1px dashed #e8ebf7; background: #fcfcff; padding: 9px 13px 10px; }
.djv-basis-h { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.djv-basis-h b { font-size: 9px; font-weight: 600; letter-spacing: 0.07em; text-transform: uppercase; color: #6b7280; }
.djv-basis-h span:last-child { font-family: Consolas, monospace; font-size: 8.5px; color: #b0b6c1; }
.djv-basis-h .sp { flex: 1; }
.djv-basis p {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.8;
  color: #1f2937;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}
.djv-dims { border-top: 1px dashed #e8ebf7; padding: 8px 13px 10px; }
.djv-dims-h { display: flex; align-items: center; gap: 8px; margin-bottom: 3px; }
.djv-dims-h b { font-size: 9px; font-weight: 600; letter-spacing: 0.07em; text-transform: uppercase; color: #6b7280; }
.djv-dims-h span:last-child { font-size: 8.5px; color: #b0b6c1; }
.djv-dims-h .sp { flex: 1; }
.djv-note { margin-top: 5px; font-size: 9px; line-height: 1.7; color: #9ca3af; }

.attrib {
  border: 1px solid #eceef3;
  background: #fbfbfd;
  border-radius: 9px;
  padding: 8px 10px;
}

.lk { display: inline-flex; align-items: center; gap: 4px; border: 0; background: none; padding: 0; font-size: 9.5px; color: #4f46e5; cursor: pointer; }
.lk:hover { text-decoration: underline; text-underline-offset: 2px; }

.dtable-wrap { border: 1px solid #eceef3; border-radius: 9px; overflow: hidden; }
.dtable { width: 100%; border-collapse: collapse; font-size: 10px; table-layout: fixed; }
.dtable th {
  text-align: left;
  font-weight: 500;
  font-size: 8.5px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: #9ca3af;
  background: #fafbfd;
  padding: 5px 9px;
  border-bottom: 1px solid #eceef3;
}
.dtable td { padding: 5px 9px; border-bottom: 1px solid #f3f3f7; color: #374151; vertical-align: top; word-break: break-word; overflow-wrap: anywhere; }
.dtable tr:last-child td { border-bottom: 0; }
.dtable td.mono { font-family: Consolas, monospace; font-size: 9.5px; color: #6b7280; width: 34%; }

.rulelist { display: flex; flex-direction: column; gap: 4px; }
.ruleline {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  border: 1px solid #eceef3;
  border-radius: 8px;
  background: #fbfbfd;
  padding: 6px 9px;
  font-size: 10.5px;
  color: #374151;
  line-height: 1.65;
  min-width: 0;
}
.ruleline.bad { border-color: #fde68a; background: #fffdf5; }
.ruleline .ri { color: #059669; display: inline-flex; margin-top: 1px; flex: none; }
.ruleline.bad .ri { color: #d97706; }
.ruleline > div { min-width: 0; word-break: break-word; overflow-wrap: anywhere; }
.ruleline .why { display: block; font-size: 9px; color: #b45309; margin-top: 1px; }
.ruleline .ra { display: block; font-size: 9px; color: #9ca3af; margin-top: 2px; font-family: Consolas, monospace; word-break: break-all; }
.ruleline .ra em { font-style: normal; color: #b0b6c1; margin-right: 3px; }
.ruleline .ra em + * { margin-right: 6px; }

.contract { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 7px; }
.contract > div {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid #a7f3d0;
  background: #ecfdf5;
  border-radius: 7px;
  padding: 4px 8px;
  font-size: 9.5px;
  color: #047857;
}
.contract > div.bad { border-color: #fde68a; background: #fffbeb; color: #b45309; }
.contract b { font-family: Consolas, monospace; font-size: 9px; font-weight: 600; }

.dempty {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  border: 1px dashed #e8e8ed;
  border-radius: 9px;
  background: #fcfcfe;
  padding: 11px 13px;
  font-size: 10px;
  color: #9ca3af;
  line-height: 1.7;
}
.dempty b { font-size: 11px; color: #6b7280; font-weight: 600; }
.dempty p { margin: 0; }
.dempty code { font-family: Consolas, monospace; color: #6b7280; }
.empty { border: 1px dashed #e8e8ed; border-radius: 10px; background: #fcfcfe; padding: 22px; text-align: center; font-size: 11px; color: #9ca3af; }

.dim { display: grid; grid-template-columns: 132px minmax(0, 1fr) 62px; align-items: center; gap: 9px; padding: 4px 0; font-size: 10px; color: #6b7280; }
.dim > span { display: inline-flex; align-items: baseline; gap: 4px; }
.dim > span em { font-style: normal; font-size: 8.5px; color: #b0b6c1; }
.dim .bar { height: 4px; border-radius: 999px; background: #eef2ff; overflow: hidden; }
.dim .bar i { display: block; height: 100%; background: linear-gradient(90deg, #a5b4fc, #6366f1); border-radius: 999px; }
.dim .bar i.low { background: linear-gradient(90deg, #fcd34d, #f59e0b); }
.dim b { text-align: right; font-family: Consolas, monospace; font-size: 9.5px; font-weight: 600; color: #374151; }
.dim b small { color: #b0b6c1; font-weight: 400; }

.jtagrow { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; }
.jtagrow + .jtagrow { margin-top: 5px; }
.jscope {
  flex: none;
  font-size: 9px;
  font-weight: 600;
  color: #4b5563;
  background: #eef0f4;
  border-radius: 5px;
  padding: 1px 7px;
  min-width: 48px;
  text-align: center;
}
.etag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 9.5px;
  color: #374151;
  background: #fff;
  border: 1px solid #e8e8ed;
  border-radius: 999px;
  padding: 1.5px 8px;
  cursor: pointer;
}
.etag i { width: 5px; height: 5px; border-radius: 50%; background: #cbd5e1; }
.etag.rose i { background: #e11d48; }
.etag.amber i { background: #f59e0b; }
.etag.sky i { background: #0ea5e9; }
.etag.violet i { background: #8b5cf6; }
.etag.orange i { background: #f97316; }
.etag-note { font-size: 8.5px; color: #b0b6c1; white-space: nowrap; }
.etag:hover { border-color: #c7d2fe; }
.etag.focus { border-color: #4f46e5; box-shadow: 0 0 0 2px rgba(79, 70, 229, 0.14); background: #f8f9ff; }
.etag-none { font-size: 9.5px; color: #b0b6c1; }

.dnote {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-top: 10px;
  font-size: 10px;
  line-height: 1.6;
  color: #9ca3af;
  word-break: break-word;
  overflow-wrap: anywhere;
}
.dnote :deep(svg) { margin-top: 1px; flex: none; color: #4f46e5; }
.dnote code { font-family: Consolas, monospace; font-size: 9px; }
/* 与原型 .dnote2 一致：异常提示用琥珀色描边块，普通说明保持无边框 */
.dnote2 {
  margin-top: 7px;
  font-size: 9.5px;
  line-height: 1.6;
  color: #b45309;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 7px;
  padding: 6px 8px;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.jlist { display: flex; flex-direction: column; gap: 4px; }
.jrow {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  border: 1px solid #eceef3;
  border-radius: 8px;
  background: #fbfbfd;
  padding: 6px 9px;
  font-size: 9.5px;
  color: #6b7280;
}
.jrow .jm { font-family: Consolas, monospace; color: #374151; font-weight: 500; }
.jrow .jp { border-radius: 999px; padding: 0 6px; border: 1px solid #e8e8ed; background: #fff; }
.jrow .jp.ok { color: #047857; border-color: #a7f3d0; background: #ecfdf5; }
.jrow .jp.bad { color: #be123c; border-color: #fecdd3; background: #fff1f2; }
.jrow .js { font-family: Consolas, monospace; color: #374151; margin-left: auto; }
.jrow .jt { font-family: Consolas, monospace; font-size: 8.5px; color: #b0b6c1; }

.dr-payload { border: 1px solid #eceef3; border-radius: 9px; overflow: hidden; }
.djitem + .djitem { border-top: 1px solid #f0f0f4; }
.djrow {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  border: 0;
  background: #fbfbfd;
  padding: 7px 10px;
  font-size: 10px;
  color: #374151;
  cursor: pointer;
  text-align: left;
}
.djrow:hover { background: #f6f7fb; }
.djrow .sp { flex: 1; }
.djrow b { font-family: Consolas, monospace; font-size: 8.5px; font-weight: 400; color: #9ca3af; }
.djrow .go { color: #c3c9d4; font-size: 12px; }
.djbody { padding: 0 10px 8px; background: #fff; }

.dtabs {
  display: flex;
  align-items: center;
  gap: 2px;
  border-bottom: 1px solid #eceef3;
  margin-bottom: 12px;
  overflow-x: auto;
  scrollbar-width: none;
}
.dtabs::-webkit-scrollbar { display: none; }
.dtabs button {
  flex: none;
  border: 0;
  border-bottom: 2px solid transparent;
  background: none;
  padding: 6px 9px 7px;
  font-size: 10px;
  color: #6b7280;
  cursor: pointer;
  white-space: nowrap;
}
.dtabs button:hover { color: #4f46e5; }
.dtabs button.on { color: #4f46e5; border-bottom-color: #4f46e5; font-weight: 600; }
.dtabs .tbsep { flex: none; width: 1px; height: 12px; background: #e8e8ed; margin: 0 4px; }

.mquote { border: 1px solid #e0e7ff; background: #f8f9ff; border-radius: 9px; padding: 8px 10px; margin-bottom: 2px; }
.mquote .ml { display: flex; align-items: center; gap: 4px; font-size: 8.5px; letter-spacing: 0.06em; text-transform: uppercase; color: #818cf8; margin-bottom: 4px; }
.mquote .mt { font-size: 11px; line-height: 1.75; color: #374151; white-space: pre-wrap; word-break: break-word; overflow-wrap: anywhere; }
.mquote .mvars { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.mquote .mvars span { font-family: Consolas, monospace; font-size: 8.5px; background: #fff; border: 1px solid #e0e7ff; border-radius: 5px; padding: 1px 5px; color: #6b7280; word-break: break-all; }

.mhd-line { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; font-size: 9px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: #9ca3af; margin: 2px 0 8px; }
.mhd-line .sp { flex: 1; }
.mhd-line .badge { letter-spacing: 0; text-transform: none; font-weight: 500; font-size: 9px; color: #6b7280; background: #f4f4f7; border-radius: 5px; padding: 1px 6px; }
.mcopy {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid #e8e8ed;
  background: #fff;
  border-radius: 6px;
  padding: 2px 7px;
  font-size: 9px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  color: #6b7280;
  cursor: pointer;
}
.mcopy:hover { border-color: #e0e7ff; color: #4f46e5; }

.dchain { display: flex; flex-direction: column; gap: 0; border-left: 2px solid #eef2ff; margin-left: 4px; }
.dchain .ln { display: grid; grid-template-columns: 76px minmax(0, 1fr); gap: 9px; padding: 5px 0 5px 10px; font-size: 10px; line-height: 1.7; position: relative; }
.dchain .ln::before { content: ""; position: absolute; left: -5px; top: 12px; width: 7px; height: 7px; border-radius: 50%; background: #c7d2fe; border: 2px solid #fff; }
.dchain .k { color: #9ca3af; font-size: 9px; }
.dchain .v { color: #374151; min-width: 0; word-break: break-word; overflow-wrap: anywhere; }
.dchain .v em { display: block; font-style: normal; font-size: 9px; color: #9ca3af; margin-top: 1px; }

.dverdict { display: flex; align-items: flex-start; gap: 8px; border: 1px solid #e8e8ed; background: #fbfbfd; border-radius: 9px; padding: 8px 10px; margin-bottom: 7px; }
.dverdict.warn { border-color: #fde68a; background: #fffdf5; }
.dverdict :deep(svg) { margin-top: 2px; color: #6b7280; }
.dverdict.warn :deep(svg) { color: #d97706; }
.dverdict b { display: block; font-size: 10.5px; color: #374151; margin-bottom: 1px; }
.dverdict span { display: block; font-size: 10px; line-height: 1.7; color: #6b7280; white-space: pre-wrap; word-break: break-word; overflow-wrap: anywhere; }

.dr-foot {
  flex: none;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  padding: 7px 18px;
  border-top: 1px solid #f0f0f4;
  background: #fcfcfe;
  font-size: 8.5px;
  color: #b0b6c1;
  font-family: Consolas, monospace;
}
.dr-foot .sp { flex: 1; }
.dr-foot .trace { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

@media (max-width: 700px) {
  .dr-head { padding: 11px 13px 0; }
  .dr-body { padding: 12px 13px 18px; }
  .dr-foot { padding: 7px 13px; }
  .dgrid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .dkv { grid-template-columns: 1fr; }
  .dim { grid-template-columns: 104px minmax(0, 1fr) 56px; }
  .dquicks button { flex: 1 1 100%; }
}
</style>

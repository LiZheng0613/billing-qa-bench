<script setup lang="ts">
/**
 * 判分页：与 design/case-turn-flow-final.html 的 renderJudge 一一对应。
 *
 * 数据口径：
 * - 轮次级分数 / 四维 / 归因 / 契约命中若后端提供（judge_score 等可选字段）则逐轮展示；
 * - 未提供时保留同样的版面，单元格显示 — 并注明「执行级口径」，不伪造归属。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ExecutionDetail } from '../../../types'
import {
  DIMENSIONS,
  SCOPE_META,
  TAG_TONE,
  VERDICT_META,
  type TraceModel,
  type TurnVM,
  type Verdict,
} from './model'
import TraceIcon from './TraceIcon.vue'
import type { DrawerSection, EvidTab } from './useTraceView'

const props = defineProps<{
  model: TraceModel
  detail: ExecutionDetail
  score: number | null
  pageDims: Array<{ key: string; label: string; value: number }>
}>()

const emit = defineEmits<{
  open: [index: number, section: DrawerSection, tag?: string, evidTab?: EvidTab]
  locate: [index: number]
  copy: [text: string]
}>()

const turns = computed(() => props.model.turns)
const passLine = computed(() => props.model.passLine)
const scored = computed(() => turns.value.filter((turn) => turn.score !== null))
const perTurnDims = computed(() => turns.value.length > 0 && turns.value.every((turn) => turn.dimensions.length === 4))

const overall = computed<number | null>(() => {
  if (scored.value.length) {
    return scored.value.reduce((sum, turn) => sum + (turn.score ?? 0), 0) / scored.value.length
  }
  return props.score
})

const passCount = computed(() => turns.value.filter((turn) => turn.verdict === 'pass').length)
const warnCount = computed(() => turns.value.filter((turn) => turn.verdict === 'warn').length)
const failCount = computed(() => turns.value.filter((turn) => turn.verdict === 'fail').length)
const unknownCount = computed(() => turns.value.filter((turn) => turn.verdict === 'unknown').length)
const allUnknown = computed(() => turns.value.length > 0 && unknownCount.value === turns.value.length)

/**
 * 执行级结论：`pass_flag` 是后端按「硬门槛全过 + 综合分 ≥ 阈值」算出的权威值。
 * 多轮执行里，没有轮次级规则/判分的中间轮会落「未判定」，若按「每轮都通过」口径
 * 汇总就会把已通过的整通执行说成「部分通过」，和页头状态、门槛流程条自相矛盾
 * （900002 / 900007 就是这种形态）。轮次的混合结论只在「轮次结论」卡与矩阵里呈现。
 */
const execPassed = computed<boolean | null>(() => {
  if (typeof props.detail.pass_flag === 'boolean') return props.detail.pass_flag
  if (overall.value === null || passLine.value === null) return null
  return overall.value >= passLine.value
})

const verdictLabel = computed(() => {
  // 没有轮次也没有任何分数：执行异常 / 终止时 pass_flag 会被写成 false，
  // 但后端从未产出裁判结论，不能据此说「未通过」。
  if (!turns.value.length && props.score === null) return '未判定'
  // 执行级结论只认 pass_flag（缺失时才回落到「综合分 ≥ 通过线」）；
  // 多轮全轮「未判定」只影响逐轮，不能覆盖整通执行已经落库的通过 / 未通过。
  if (execPassed.value === null) return '未判定'
  return execPassed.value ? '通过' : '未通过'
})
const verdictTone = computed<Verdict>(() => {
  if (!turns.value.length && props.score === null) return 'unknown'
  if (execPassed.value === null) return 'unknown'
  return execPassed.value ? 'pass' : 'fail'
})

/** 维度均值：逐轮有分时按轮平均，否则退回执行级四维并标注口径 */
const dimCards = computed(() =>
  DIMENSIONS.map((dim) => {
    if (perTurnDims.value) {
      const values = turns.value.map((turn) => turn.dimensions.find((item) => item.key === dim.key)?.value ?? 0)
      const avg = values.reduce((sum, value) => sum + value, 0) / values.length
      const minIndex = values.reduce((best, value, index) => (value < values[best] ? index : best), 0)
      return {
        key: dim.key,
        label: dim.label,
        max: dim.max,
        value: avg,
        note:
          turns.value.length === 1
            ? `占满分 ${Math.round((avg / dim.max) * 100)}%`
            : `最低 T${turns.value[minIndex]?.index ?? '—'} · ${values[minIndex] ?? 0}/${dim.max}`,
        source: 'turn' as const,
      }
    }
    const execution = props.pageDims.find((item) => item.key === dim.key)
    return {
      key: dim.key,
      label: dim.label,
      max: dim.max,
      value: execution?.value ?? null,
      note: execution ? '执行级口径 · 不拆轮次' : '后端未提供',
      source: 'exec' as const,
    }
  }),
)

/** 没有任何执行级/轮次级四维数据时（如历史 RUBRIC 记录）不渲染四张空卡 */
const hasDimData = computed(() => dimCards.value.some((dim) => dim.value !== null))

const ruleFail = computed(() =>
  turns.value.reduce((sum, turn) => sum + turn.rules.filter((rule) => !rule.pass).length, 0),
)
const rulePassed = computed(() =>
  turns.value.reduce((sum, turn) => sum + turn.rules.filter((rule) => rule.pass).length, 0),
)
const ruleTotal = computed(() => turns.value.reduce((sum, turn) => sum + turn.rules.length, 0))

/**
 * 执行级答案契约：后端只给整通执行的 facts，没有轮次级 contract 计数。
 * 模型把这类事实挂在最后一轮（factsScope = execution），统计与渲染都必须认这个口径，
 * 否则「未通过」的执行会显示成「契约 —、0 条错误」，看不到真正卡住的门槛。
 */
const execFactsTurn = computed(
  () => turns.value.find((turn) => turn.factsScope === 'execution' && !turn.contract) ?? null,
)
const execFacts = computed(() => execFactsTurn.value?.facts ?? [])
const hasTurnContract = computed(
  () => turns.value.some((turn) => turn.contract !== null) || execFacts.value.length > 0,
)
const contractPassed = computed(
  () =>
    turns.value.reduce((sum, turn) => sum + (turn.contract?.passed ?? 0), 0) +
    execFacts.value.filter((fact) => fact.pass).length,
)
const contractTotal = computed(
  () =>
    turns.value.reduce((sum, turn) => sum + (turn.contract?.total ?? 0), 0) + execFacts.value.length,
)
const contractFail = computed(() => contractTotal.value - contractPassed.value)

const sessionTagAgg = computed(() => aggregateTags('session'))
const turnTagAgg = computed(() => aggregateTags('turn'))
const sessionTagTotal = computed(() => sessionTagAgg.value.length)
const tagTotal = computed(
  () => turns.value.reduce((sum, turn) => sum + turn.tags.length, 0) + sessionTagTotal.value,
)

interface TagAgg {
  name: string
  turns: number[]
  /** 会话级归因没有轮次字段：只去重，不累计轮次、不跳轮 */
  perTurn: boolean
}

function aggregateTags(scope: 'session' | 'turn'): TagAgg[] {
  const map = new Map<string, TagAgg>()
  for (const turn of turns.value) {
    const list = scope === 'session' ? turn.sessionTags : turn.tags
    for (const name of list) {
      if (!name) continue
      const hit = map.get(name)
      if (scope === 'session') {
        // 执行级 error_tags 会被复制到每一轮，累计轮次会伪造「×N / T1」归属
        if (!hit) map.set(name, { name, turns: [], perTurn: false })
        continue
      }
      if (hit) hit.turns.push(turn.index)
      else map.set(name, { name, turns: [turn.index], perTurn: true })
    }
  }
  return [...map.values()]
}

const lowest = computed<TurnVM | null>(() => {
  const list = scored.value
  if (!list.length) return null
  return list.reduce((acc, turn) => ((turn.score ?? 0) < (acc.score ?? 0) ? turn : acc), list[0])
})

const conclusionParts = computed<Array<{ text: string; strong?: boolean }>>(() => {
  const total = turns.value.length
  if (!total) {
    // 没有轮次也没有分数：异常 / 终止执行落库时 pass_flag 会被后端写成 false，
    // 但从未产出裁判结论，不能沿用旧 RUBRIC 的「只保留执行级分数与结论」话术。
    if (props.score === null) {
      return [{ text: '本次执行没有产生对话轮次，也没有执行级评分与结论。' }]
    }
    // 旧 RUBRIC 执行（当前库 1226 条）只有执行级分数与结论，说清缺什么，不拿「没有轮次」一句带过
    if (props.model.gradingMode === 'RUBRIC') {
      const scoreText = props.score.toFixed(1)
      return [
        {
          text: `本次执行由旧评分模式（RUBRIC）落库：只保留执行级综合分 ${scoreText} 与通过结论，没有轮次明细、裁判理由与四维得分。`,
        },
      ]
    }
    return [{ text: '本次执行没有对话轮次。' }]
  }
  const overallText = overall.value === null ? '—' : overall.value.toFixed(1)
  if (total === 1) {
    const turn = turns.value[0]
    if (turn.verdict === 'unknown') {
      return [
        { text: '本轮未判定：' },
        { text: '后端没有提供本轮分数与硬门槛，不据此推断本轮通过。' },
      ]
    }
    const weak = turn.dimensions
      .filter((dim) => dim.value / dim.max < 0.8)
      .map((dim) => `${dim.label} ${dim.value}/${dim.max}`)
    // 单轮结论跟轮次 verdict 走：82 分但规则未过是「部分命中」，不是「未通过」
    const parts: Array<{ text: string; strong?: boolean }> = [
      { text: turn.verdict === 'pass' ? '本轮通过' : turn.verdict === 'warn' ? '本轮部分命中' : '本轮未通过' },
      { text: '：总分 ' },
      { text: `${overallText} / 100`, strong: true },
    ]
    if (turn.verdict === 'warn') {
      parts.push({ text: '；达线但硬门槛未全过' })
      if (weak.length) parts.push({ text: `，${weak.join('、')} 低于 80%` })
      parts.push({ text: '。' })
    } else if (weak.length) {
      parts.push({ text: `；${weak.join('、')} 低于 80%。` })
    } else {
      parts.push({ text: '。' })
    }
    return parts
  }
  if (allUnknown.value) {
    const execVerdict =
      execPassed.value === true ? '通过' : execPassed.value === false ? '未通过' : null
    return [
      { text: `共 ${total} 轮，后端未提供轮次级分数与硬门槛，无法判定逐轮结论；执行级综合分 ` },
      { text: `${overallText} / 100`, strong: true },
      {
        text: execVerdict
          ? ` 仅作参考，整通执行按记录判「${execVerdict}」。`
          : ' 仅作参考，不据此推断某轮通过。',
      },
    ]
  }
  const parts: Array<{ text: string; strong?: boolean }> = [
    { text: `共 ${total} 轮，${passCount.value} 轮通过，总分 ` },
    { text: `${overallText} / 100`, strong: true },
    { text: '。' },
  ]
  if (lowest.value) {
    const low = lowest.value
    // 会话级归因不归属某一轮：最低项只说该轮自己的轮次级归因
    const tags = low.tags
    parts.push({ text: '最低项为 ' })
    parts.push({ text: `T${low.index} ${low.score ?? '—'} 分`, strong: true })
    if (tags.length) parts.push({ text: `（${tags.join('、')}）` })
    parts.push({ text: '；' })
  }
  if (unknownCount.value) {
    parts.push({ text: `${unknownCount.value} 轮缺少轮次级门槛与分数，标记为未判定；` })
  }
  parts.push({
    text:
      execPassed.value === false
        ? '硬门槛未全部通过，结论为「未通过」。'
        : '硬门槛全部通过，按总分定档。',
  })
  return parts
})

interface GateItem {
  turn: number
  kind: 'rule' | 'contract'
  title: string
  why: string
  tone: string
}

const failedContractGates = computed(() =>
  props.model.contract.exists
    ? props.model.contract.gates.filter((gate) => !gate.passed && gate.key !== 'facts')
    : [],
)
const lastTurnIndex = computed(() => turns.value[turns.value.length - 1]?.index ?? null)

const gates = computed<GateItem[]>(() => {
  const rows: GateItem[] = []
  for (const turn of turns.value) {
    for (const rule of turn.rules) {
      if (!rule.pass) {
        rows.push({
          turn: turn.index,
          kind: 'rule',
          title: rule.name,
          why: rule.why || '未命中',
          tone: 'rule',
        })
      }
    }
    if (turn.contract && turn.contract.passed < turn.contract.total) {
      const failedFact = turn.facts.find((fact) => !fact.pass)
      rows.push({
        turn: turn.index,
        kind: 'contract',
        title: `facts ${turn.contract.passed}/${turn.contract.total} · ${failedFact?.label ?? '答案契约'}`,
        why: failedFact?.reason || '存在未命中的事实',
        tone: 'contract',
      })
    }
    if (turn.factsScope === 'execution' && !turn.contract) {
      const failedFact = turn.facts.find((fact) => !fact.pass && fact.isGate)
      if (failedFact) {
        rows.push({
          turn: turn.index,
          kind: 'contract',
          title: `执行级 facts ${turn.facts.filter((fact) => fact.pass).length}/${turn.facts.length} · ${failedFact.label}`,
          why: failedFact.reason || '存在未命中的事实',
          tone: 'contract',
        })
      }
    }
  }
  // facts 已在上面按失败事实展开；这里补上同一份契约记录里的其他失败闸门（安全 / 行为 / 扩展），
  // 不能让 900006 这种 gates.safety=false 的执行只看到 facts 一项。
  if (lastTurnIndex.value !== null) {
    for (const gate of failedContractGates.value) {
      rows.push({
        turn: lastTurnIndex.value,
        kind: 'contract',
        title: `${gate.label}闸门`,
        why: `${gate.label}闸门未通过（执行级契约）`,
        tone: 'contract',
      })
    }
  }
  return rows.sort((a, b) => a.turn - b.turn || (a.kind === 'rule' ? -1 : 1))
})

const expanded = ref<number | null>(pickDefault())
const selected = computed(() => turns.value.find((turn) => turn.index === expanded.value) ?? null)

/** 本轮 Agent 回复正文：与对话页同口径，只去掉内部 :icon: 标记，不改原文。 */
const selectedAnswer = computed(() => (selected.value?.answer ?? '').replace(/:icon:[a-z-]+:/gi, '').trim())

/** 本轮裁判理由：优先轮次级，单轮执行或后端只给会话级时回落并标注口径。 */
const selectedReason = computed(() => {
  const turn = selected.value
  if (!turn) return ''
  if (turn.judgeReason) return turn.judgeReason
  return turns.value.length > 1 ? (turn.sessionJudgeReason ?? '') : ''
})
const reasonIsSession = computed(() => Boolean(selected.value && !selected.value.judgeReason && selectedReason.value))

/**
 * 长文本折叠：回复与裁判理由默认按固定行高截断，超长时给「展开全文」。
 * 是否溢出用实际 scrollHeight 判断（阈值与 CSS max-height 对齐），避免按字数猜。
 * 遮罩与 max-height 都只在真实溢出时挂 clamp：文字正好放得下时不能虚化最后一行
 * （否则看起来像还有内容，却没有「展开」按钮）。
 */
const ANSWER_CLAMP = 143
const REASON_CLAMP = 137
const rootEl = ref<HTMLElement | null>(null)
const answerOpen = ref(false)
const reasonOpen = ref(false)
const answerOverflow = ref(false)
const reasonOverflow = ref(false)

/**
 * 从组件根节点查询当前展开行的正文节点：展开行在切换轮次时会被重建，
 * 用元素级 ref 会读到已卸载的旧节点（实测 switch 后 ref 为 null，折叠按钮丢失）。
 */
function measureOverflow(): void {
  const root = rootEl.value
  const body = root?.querySelector<HTMLElement>('.jx-ai-body') ?? null
  const reason = root?.querySelector<HTMLElement>('.jx-judge p') ?? null
  answerOverflow.value = !!body && body.scrollHeight > ANSWER_CLAMP + 4
  reasonOverflow.value = !!reason && reason.scrollHeight > REASON_CLAMP + 4
}

function remeasure(): void {
  void nextTick(measureOverflow)
}

function pickDefault(): number | null {
  const list = props.model.turns
  if (!list.length) return null
  const scoredList = list.filter((turn) => turn.score !== null)
  if (scoredList.length) {
    return scoredList.reduce((acc, turn) => ((turn.score ?? 0) < (acc.score ?? 0) ? turn : acc), scoredList[0]).index
  }
  const abnormal = list.find((turn) => turn.verdict === 'fail' || turn.verdict === 'warn')
  return (abnormal ?? list[list.length - 1]).index
}

watch(
  () => props.detail.id,
  () => {
    expanded.value = pickDefault()
  },
)

function toggleRow(index: number): void {
  expanded.value = expanded.value === index ? null : index
}

/** 切换轮次时收起长文本，并按新内容重算是否溢出 */
watch(expanded, () => {
  answerOpen.value = false
  reasonOpen.value = false
  remeasure()
})

watch([selectedAnswer, selectedReason], remeasure)

/**
 * 宽度变化（侧栏展开 / 抽屉打开）不触发 window resize，用 ResizeObserver 补测。
 * 观察组件根节点（稳定存在），不观察会被重建的正文节点。
 */
let sizeObserver: ResizeObserver | null = null

onMounted(() => {
  sizeObserver = new ResizeObserver(measureOverflow)
  if (rootEl.value) sizeObserver.observe(rootEl.value)
  window.addEventListener('resize', measureOverflow)
  remeasure()
})

onBeforeUnmount(() => {
  sizeObserver?.disconnect()
  sizeObserver = null
  window.removeEventListener('resize', measureOverflow)
})

function heat(percent: number): 'g' | 'n' | 'a' | 'r' {
  if (percent >= 90) return 'g'
  if (percent >= 80) return 'n'
  if (percent >= 70) return 'a'
  return 'r'
}

function dimHeat(turn: TurnVM, key: string): string {
  const dim = turn.dimensions.find((item) => item.key === key)
  if (!dim) return ''
  return heat((dim.value / dim.max) * 100)
}

function dimValue(turn: TurnVM, key: string): string {
  return String(turn.dimensions.find((item) => item.key === key)?.value ?? '—')
}

function scoreHeat(turn: TurnVM): string {
  if (turn.verdict === 'unknown') return 'unknown'
  return turn.verdict
}

function tagTone(name: string): string {
  return TAG_TONE[name] ?? 'slate'
}

function tagTitle(agg: TagAgg): string {
  if (!agg.perTurn) return `${agg.name} · 会话级归因 · 未拆分到轮次`
  return `${agg.name} · 出现于 T${agg.turns.join(' · T')} · 打开该轮判定段`
}

function openTag(agg: TagAgg): void {
  emit('open', agg.turns[0], 'judge', agg.name)
}

function gateText(turn: TurnVM): string {
  if (turn.verdict === 'unknown') return '未判定'
  return turn.pass ? '已通过' : '未通过'
}

function dimPercent(value: number | null, max: number): number {
  if (value === null) return 0
  return Math.min(100, Math.max(0, (value / max) * 100))
}

function conclusionText(turn: TurnVM): string {
  const gate = turn.contract
    ? `规则 ${turn.rules.filter((rule) => rule.pass).length}/${turn.rules.length} · 契约 ${turn.contract.passed}/${turn.contract.total}`
    : `规则 ${turn.rules.filter((rule) => rule.pass).length}/${turn.rules.length}`
  const reason = turn.judgeReason
    || (props.model.turns.length > 1 && turn.sessionJudgeReason ? `（会话级，不归属本轮）${turn.sessionJudgeReason}` : '')
    || '后端未提供本轮裁判理由。'
  return `T${turn.index} 结论：${VERDICT_META[turn.verdict].label}\n得分：${turn.score ?? '—'} / 100\n硬门槛：${gate}\n${reason}`
}

function copyConclusion(turn: TurnVM): void {
  emit('copy', conclusionText(turn))
}
</script>

<template>
  <div ref="rootEl" class="judge2">
    <!-- ===== 概览：结论 + 四维 + 关键计数 + LLM 结论 + 归因 ===== -->
    <div class="jsum">
      <div class="jvcol">
        <span class="stat-pill" :class="verdictTone">
          <TraceIcon :name="verdictTone === 'pass' ? 'check' : verdictTone === 'unknown' ? 'info' : 'alert'" :size="10" />
          {{ verdictLabel }}
        </span>
        <div class="row">
          <span class="sc">{{ overall === null ? '—' : overall.toFixed(1) }}</span>
          <span class="u">/ 100</span>
        </div>
      </div>

      <div class="jmid">
        <div v-if="hasDimData" class="jdimrow">
          <div
            v-for="dim in dimCards"
            :key="dim.key"
            class="jsd"
            :class="{ low: dim.value !== null && dim.value / dim.max < 0.8 }"
          >
            <span class="n">{{ dim.label }}<em>{{ dim.max }}%</em></span>
            <div class="row2">
              <span class="v">{{ dim.value === null ? '—' : dim.value.toFixed(1) }}</span>
              <span class="u">/ {{ dim.max }}</span>
            </div>
            <div class="bar">
              <i
                :class="{ low: dim.value !== null && dim.value / dim.max < 0.8 }"
                :style="{ width: `${dimPercent(dim.value, dim.max)}%` }"
              />
            </div>
            <span class="t">{{ dim.note }}</span>
          </div>
        </div>

        <div class="jtxt">
          <span class="t">LLM 综合结论</span>
          <template v-for="(part, index) in conclusionParts" :key="index">
            <b v-if="part.strong">{{ part.text }}</b>
            <template v-else>{{ part.text }}</template>
          </template>

          <div v-if="turns.length" class="jtags">
            <b class="jtags-h">错误归因</b>
            <div class="jtagrow">
              <span class="jscope" :title="SCOPE_META.session.hint">会话级</span>
              <template v-if="sessionTagAgg.length">
                <span
                  v-for="agg in sessionTagAgg"
                  :key="agg.name"
                  class="etag"
                  :class="tagTone(agg.name)"
                  :title="tagTitle(agg)"
                >
                  <i />
                  {{ agg.name }}
                </span>
                <span class="etag-note">未拆分到轮次</span>
              </template>
              <span v-else class="etag-none">无</span>
            </div>
            <div class="jtagrow">
              <span class="jscope" :title="SCOPE_META.turn.hint">轮次级</span>
              <template v-if="turnTagAgg.length">
                <button
                  v-for="agg in turnTagAgg"
                  :key="agg.name"
                  type="button"
                  class="etag tag-btn"
                  :class="tagTone(agg.name)"
                  :title="tagTitle(agg)"
                  @click="openTag(agg)"
                >
                  <i />
                  {{ agg.name }}
                  <b v-if="agg.turns.length > 1" class="c">×{{ agg.turns.length }}</b>
                  <b class="c">T{{ agg.turns[0] }}</b>
                </button>
              </template>
              <span v-else class="etag-none">无</span>
            </div>
          </div>

          <div v-if="turns.length" class="gate-flow">
            <span class="g" :class="{ bad: execPassed === false && !allUnknown }">
              硬门槛 规则 <b>{{ rulePassed }}/{{ ruleTotal }}</b> · 契约
              <b>{{ hasTurnContract ? `${contractPassed}/${contractTotal}` : '—' }}</b>
            </span>
            <span class="arr">→</span>
            <span class="g">总分 <b>{{ overall === null ? '—' : overall.toFixed(1) }}</b> / 100</span>
            <span class="arr">→</span>
            <span class="g res" :class="verdictTone">{{ verdictLabel }}</span>
          </div>
        </div>
      </div>

      <div v-if="turns.length" class="jchips">
        <span class="passchip">轮次通过 <b>{{ passCount }} / {{ turns.length }}</b></span>
        <span v-if="warnCount" class="warnchip">部分命中 <b>{{ warnCount }}</b></span>
        <span v-if="failCount" class="failchip">未通过 <b>{{ failCount }}</b></span>
        <span v-if="unknownCount">未判定 <b>{{ unknownCount }}</b></span>
        <span v-if="Number(detail.judge_result?.agreement) > 0">
          双裁判一致 <b>{{ detail.judge_result?.agreement }}/{{ turns.length }}</b>
        </span>
        <span v-else-if="detail.judges.length > 1">
          裁判数量 <b>{{ detail.judges.length }}</b>
        </span>
        <span v-else>评分来源 <b>单裁判</b></span>
        <span>规则失败 <b>{{ ruleFail }}</b> 条</span>
        <span>契约未命中 <b>{{ hasTurnContract ? contractFail : '—' }}</b> 项</span>
        <span>错误归因 <b>{{ tagTotal }}</b> 条</span>
      </div>
    </div>

    <!-- ===== 未通过门槛 ===== -->
    <div v-if="gates.length" class="jfails">
      <div class="jf-h">
        <b>未通过门槛</b>
        <span class="n">{{ gates.length }} 项</span>
        <span class="sp" />
        <span class="tip">硬门槛不过，总分再高也判「未通过」</span>
      </div>
      <div class="jf-grid">
        <button
          v-for="gate in gates"
          :key="`${gate.kind}-${gate.turn}-${gate.title}`"
          type="button"
          class="jf"
          :title="`展开 T${gate.turn} 的判定依据`"
          @click="expanded = gate.turn"
        >
          <span class="jf-k" :class="gate.tone">{{ gate.kind === 'rule' ? '规则' : '契约' }}</span>
          <span class="jf-main">
            <b>T{{ gate.turn }} · {{ gate.title }}</b>
            <em>{{ gate.why }}</em>
          </span>
          <span class="jf-go">展开 <TraceIcon name="chev" :size="9" /></span>
        </button>
      </div>
    </div>

    <!-- ===== 判分矩阵 ===== -->
    <div v-if="turns.length" class="jmatrix">
      <div class="jm-h">
        <b>按轮次 · 门槛 + 维度得分</b>
        <span>{{ turns.length }} 轮</span>
        <span class="sp" />
        <span class="hint">{{ turns.length === 1 ? '本轮判定依据已展开' : '点击任意行展开该轮判定依据' }}</span>
      </div>

      <table class="heat">
        <thead>
          <tr>
            <th rowspan="2" class="n">轮次</th>
            <th rowspan="2" class="vd" :title="`pass = 硬门槛全过 且 total_score ≥ ${passLine}`">结论</th>
            <th rowspan="2" class="q">提问</th>
            <th colspan="2" class="grp gate">硬门槛 · 决定 pass</th>
            <th colspan="4" class="grp llm">维度得分 · 40 / 30 / 20 / 10</th>
            <th rowspan="2" class="tags" title="此列只列轮次级归因；会话级归因见上方结论区">
              错误归因<span class="dimmax">轮次级</span>
            </th>
            <th rowspan="2">总分</th>
            <th rowspan="2" class="chev" />
          </tr>
          <tr>
            <th>规则</th>
            <th>契约</th>
            <th v-for="dim in DIMENSIONS" :key="dim.key">
              {{ dim.label }}<span class="dimmax">/{{ dim.max }}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <template v-for="turn in turns" :key="turn.index">
            <tr
              class="heat-row"
              :class="[`st-${turn.verdict}`, { sel: turn.index === expanded }]"
              :data-jrow="turn.index"
              @click="toggleRow(turn.index)"
            >
              <td class="n">T{{ turn.index }}</td>
              <td class="vd">
                <span class="stat-pill" :class="turn.verdict">
                  <TraceIcon
                    :name="turn.verdict === 'pass' ? 'check' : turn.verdict === 'unknown' ? 'info' : turn.verdict === 'warn' ? 'alert' : 'x'"
                    :size="9"
                  />
                  {{ VERDICT_META[turn.verdict].label }}
                </span>
              </td>
              <td class="q" :title="turn.query">
                <span class="qline">{{ turn.query || '—' }}</span>
              </td>
              <td class="gate">
                <span v-if="turn.rules.length" class="cell" :class="turn.rules.every((rule) => rule.pass) ? 'g' : 'a'">
                  {{ turn.rules.filter((rule) => rule.pass).length }}/{{ turn.rules.length }}
                </span>
                <span v-else class="cell">—</span>
              </td>
              <td class="gate">
                <span v-if="turn.contract" class="cell" :class="turn.contract.passed >= turn.contract.total ? 'g' : 'a'">
                  {{ turn.contract.passed }}/{{ turn.contract.total }}
                </span>
                <span
                  v-else-if="turn.factsScope === 'execution' && turn.facts.length"
                  class="cell"
                  :class="turn.facts.every((fact) => fact.pass) ? 'g' : 'a'"
                  title="答案契约按整通执行判定，这里是执行级 facts 命中数"
                >
                  {{ turn.facts.filter((fact) => fact.pass).length }}/{{ turn.facts.length }}
                </span>
                <span v-else class="cell" title="答案契约按整通执行判定，未拆到轮次">—</span>
              </td>
              <td v-for="dim in DIMENSIONS" :key="dim.key">
                <span class="cell" :class="dimHeat(turn, dim.key)">{{ dimValue(turn, dim.key) }}</span>
              </td>
              <td class="tags">
                <div v-if="turn.tags.length" class="etag-cell">
                  <button
                    v-for="tag in turn.tags.slice(0, 3)"
                    :key="tag"
                    type="button"
                    class="etag tag-btn"
                    :class="tagTone(tag)"
                    :title="`${tag} · 打开 T${turn.index} 判定段`"
                    @click.stop="emit('open', turn.index, 'judge', tag)"
                  >
                    <i />
                    {{ tag }}
                  </button>
                  <span v-if="turn.tags.length > 3" class="etag-more">+{{ turn.tags.length - 3 }}</span>
                </div>
                <span v-else class="etag-none" title="本轮无轮次级归因">—</span>
              </td>
              <td>
                <span
                  v-if="turn.score !== null"
                  class="cell total"
                  :class="scoreHeat(turn)"
                  :title="turn.verdict === 'pass' ? `通过：硬门槛全过且总分 ≥ ${passLine}` : turn.verdict === 'warn' ? '部分命中：总分达线，硬门槛未全过' : turn.verdict === 'unknown' ? '未判定：缺少轮次级门槛与分数' : `未通过：总分未达 ${passLine} 或硬门槛未过`"
                >
                  {{ turn.score }}
                </span>
                <span v-else class="cell total unknown" title="轮次总分只有单轮执行或后端提供轮次分时才有">—</span>
              </td>
              <td class="chev"><TraceIcon name="chevron-r" :size="11" /></td>
            </tr>

            <tr v-if="turn.index === expanded && selected" class="jx">
              <td colspan="12">
                <div class="jx-inner">
                  <div class="jx-main">
                    <!-- 先看本轮 Agent 实际回复，再看裁判打分；意图 / 时延在「对话轮次」分隔条与本轮详情里，这里不复述 -->
                    <div v-if="turn.query.length > 28" class="jx-q">{{ turn.query }}</div>

                    <!-- 与「对话轮次」同款气泡：头像 + 白色气泡，直接复用该页的视觉语言 -->
                    <div class="jx-ai">
                      <div class="jx-ai-av"><TraceIcon name="bot" :size="14" /></div>
                      <div class="jx-ai-bubble">
                        <div class="jx-ai-meta">
                          <span class="who">Agent</span>
                          <span class="tag">本轮回复</span>
                          <span class="sp" />
                          <span v-if="selectedAnswer" class="cnt">{{ selectedAnswer.length }} 字</span>
                        </div>
                        <div
                          v-if="selectedAnswer"
                          class="jx-ai-body"
                          :class="{ clamp: answerOverflow && !answerOpen }"
                        >{{ selectedAnswer }}</div>
                        <div v-else class="jx-ai-body empty">本轮没有回复正文（可能执行未完成或调用失败）。</div>
                        <button
                          v-if="selectedAnswer && answerOverflow"
                          type="button"
                          class="jx-more"
                          @click.stop="answerOpen = !answerOpen"
                        >
                          {{ answerOpen ? '收起' : '展开全文' }}
                          <TraceIcon name="chev" :size="10" :class="{ flip: answerOpen }" />
                        </button>
                      </div>
                    </div>

                    <div class="jd-dims">
                      <div class="jx-sec-h">
                        <TraceIcon name="grid" :size="11" />
                        <b>各项打分</b>
                        <span class="note">{{ turn.dimensions.length ? '四维 · 轮次级' : '四维 · 执行级口径' }}</span>
                      </div>
                      <div v-if="turn.dimensions.length" class="jd-dims-rows">
                        <div v-for="dim in turn.dimensions" :key="dim.key" class="jd-dim">
                          <span class="lb">{{ dim.label }}</span>
                          <div class="bar">
                            <i
                              :class="{ low: dim.value / dim.max < 0.8 }"
                              :style="{ width: `${Math.min(100, (dim.value / dim.max) * 100)}%` }"
                            />
                          </div>
                          <b :class="{ low: dim.value / dim.max < 0.8 }">{{ dim.value }}<small>/{{ dim.max }}</small></b>
                        </div>
                      </div>
                      <p v-else class="jd-dims-empty">
                        后端只落库执行级四维（见上方「维度得分」），未提供本轮拆分。
                      </p>
                    </div>

                    <div class="jx-judge">
                      <div class="jx-judge-h">
                        <span class="av"><TraceIcon name="scale" :size="11" /></span>
                        <b>裁判理由</b>
                        <span class="note">裁判模型输出原文{{ selectedReason ? ` · ${selectedReason.length} 字` : '' }}</span>
                      </div>
                      <template v-if="selectedReason">
                        <p :class="{ clamp: reasonOverflow && !reasonOpen }">{{ selectedReason }}</p>
                        <p v-if="reasonIsSession" class="jd-conc-note">会话级结论，不归属本轮。</p>
                      </template>
                      <p v-else>后端未提供本轮裁判理由。</p>
                      <button
                        v-if="reasonOverflow"
                        type="button"
                        class="jx-more"
                        @click.stop="reasonOpen = !reasonOpen"
                      >
                        {{ reasonOpen ? '收起' : '展开全文' }}
                        <TraceIcon name="chev" :size="10" :class="{ flip: reasonOpen }" />
                      </button>
                    </div>
                  </div>

                  <div class="jx-side">
                    <div class="jd-checks">
                      <div class="jdc-h">
                        门槛检查<span class="sp" />
                        <b :class="turn.pass ? 'ok' : 'bad'">{{ gateText(turn) }}</b>
                      </div>
                      <div v-for="rule in turn.rules" :key="rule.name" class="jdc" :class="rule.pass ? 'ok' : 'bad'">
                        <span class="ic"><TraceIcon :name="rule.pass ? 'check' : 'x'" :size="11" /></span>
                        <span class="nm">{{ rule.name }}</span>
                        <span v-if="!rule.pass" class="why">{{ rule.why || '未命中' }}</span>
                      </div>
                      <template v-if="turn.contract || turn.factsScope === 'execution'">
                        <div class="jdc-sep">
                          <template v-if="turn.contract">答案契约 · facts {{ turn.contract.passed }}/{{ turn.contract.total }}</template>
                          <template v-else>执行级答案契约 · facts {{ turn.facts.filter((fact) => fact.pass).length }}/{{ turn.facts.length }}</template>
                        </div>
                        <div
                          v-for="fact in turn.facts"
                          :key="fact.id || fact.label"
                          class="jdc"
                          :class="fact.pass ? 'ok' : 'bad'"
                        >
                          <span class="ic"><TraceIcon :name="fact.pass ? 'check' : 'x'" :size="11" /></span>
                          <span class="nm">{{ fact.label }}</span>
                          <span v-if="!fact.pass" class="why">{{ fact.reason || '未命中' }}</span>
                        </div>
                        <p v-if="!turn.facts.length" class="jdc-none">
                          事实明细按整通执行判定，可在「证据 · 流程裁决」查看。
                        </p>
                      </template>
                      <template v-if="turn.index === lastTurnIndex && failedContractGates.length">
                        <div class="jdc-sep">执行级契约闸门</div>
                        <div
                          v-for="gate in failedContractGates"
                          :key="gate.key"
                          class="jdc bad"
                        >
                          <span class="ic"><TraceIcon name="x" :size="11" /></span>
                          <span class="nm">{{ gate.label }}闸门</span>
                          <span class="why">未通过</span>
                        </div>
                      </template>
                      <p v-if="!turn.rules.length && !turn.contract && turn.factsScope !== 'execution'" class="jdc-none">
                        本轮没有规则或契约门槛，无法据此判定。
                      </p>
                    </div>

                    <div class="jx-attrib">
                      <div class="jx-sec-h">
                        <TraceIcon name="target" :size="11" />
                        <b>错误归因</b>
                        <span class="note">点击查看证据</span>
                      </div>
                      <div class="jx-attrib-row">
                        <b>轮次级</b>
                        <template v-if="turn.tags.length">
                          <button
                            v-for="tag in turn.tags"
                            :key="tag"
                            type="button"
                            class="etag tag-btn"
                            :class="tagTone(tag)"
                            @click="emit('open', turn.index, 'judge', tag)"
                          >
                            <i />
                            {{ tag }}
                          </button>
                        </template>
                        <span v-else class="etag-none">无</span>
                      </div>
                      <div class="jx-attrib-row">
                        <b>会话级</b>
                        <template v-if="turn.sessionTags.length">
                          <span
                            v-for="tag in turn.sessionTags"
                            :key="tag"
                            class="etag"
                            :class="tagTone(tag)"
                            :title="SCOPE_META.session.hint"
                          >
                            <i />
                            {{ tag }}
                          </span>
                          <span class="etag-note">未拆分到轮次</span>
                        </template>
                        <span v-else class="etag-none">无</span>
                      </div>
                    </div>

                    <div class="jx-acts">
                      <button type="button" class="primary" title="切到「对话轮次」并定位本轮" @click.stop="emit('locate', turn.index)">
                        <TraceIcon name="chat" :size="10" />定位到对话
                      </button>
                      <button
                        type="button"
                        title="在右侧面板查看流程裁决"
                        @click.stop="emit('open', turn.index, 'evid', undefined, 'judge')"
                      >
                        <TraceIcon name="file" :size="10" />流程裁决
                      </button>
                      <button
                        type="button"
                        title="在右侧面板查看评分详情"
                        @click.stop="emit('open', turn.index, 'judge')"
                      >
                        <TraceIcon name="scale" :size="10" />评分详情
                      </button>
                      <button type="button" title="复制本轮结论" @click.stop="copyConclusion(turn)">
                        <TraceIcon name="copy" :size="10" />复制结论
                      </button>
                    </div>
                  </div>
                </div>
              </td>
            </tr>
          </template>

          <tr v-if="turns.length > 1" class="sum">
            <td class="n" :title="perTurnDims ? '已评分轮次四维均值' : '无轮次级四维，此行为执行级汇总'">
              {{ perTurnDims ? '均分' : '执行级' }}
            </td>
            <td class="vd">
              <span class="sumvd"><b>{{ passCount }}</b>/{{ turns.length }} 通过</span>
            </td>
            <td class="q">全部轮次</td>
            <td class="gate">
              <span class="cell" :class="rulePassed < ruleTotal ? 'a' : 'g'">{{ rulePassed }}/{{ ruleTotal }}</span>
            </td>
            <td class="gate">
              <span class="cell" :class="contractPassed < contractTotal ? 'a' : 'g'">
                {{ hasTurnContract ? `${contractPassed}/${contractTotal}` : '—' }}
              </span>
            </td>
            <td v-for="dim in dimCards" :key="dim.key">
              <span class="cell" :class="dim.value === null ? '' : heat((dim.value / dim.max) * 100)">
                {{ dim.value === null ? '—' : dim.value.toFixed(1) }}
              </span>
            </td>
            <td class="tags"><span class="etag-none" :title="`轮次级 ${tagTotal - sessionTagTotal} 条 + 会话级 ${sessionTagTotal} 条`">{{ tagTotal }} 条</span></td>
            <td>
              <span class="cell total" :class="verdictTone">{{ overall === null ? '—' : overall.toFixed(1) }}</span>
            </td>
            <td class="chev" />
          </tr>
        </tbody>
      </table>

      <div class="heat-legend">
        <span class="lg"><i style="background: #a7f3d0" />≥ 90% 优</span>
        <span class="lg"><i style="background: #e2e8f0" />80–90% 良</span>
        <span class="lg"><i style="background: #fde68a" />70–80% 待改进</span>
        <span class="lg"><i style="background: #fecdd3" />&lt; 70% 短板</span>
        <span class="lg vd"><i style="background: #10b981" />通过</span>
        <span class="lg vd"><i style="background: #f59e0b" />部分命中</span>
        <span class="lg vd"><i style="background: #f43f5e" />未通过</span>
        <span v-if="unknownCount" class="lg vd"><i style="background: #94a3b8" />未判定</span>
        <span style="flex: 1" />
        <span>pass = 硬门槛全过 且 total_score ≥ {{ passLine }}</span>
      </div>

      <div class="jweak">
        <div class="jw-h">
          <b>判定口径</b>
          <span class="sp" />
          <span class="tip">门槛决定 pass，维度分按权重加总为 total_score</span>
        </div>
        <div class="jw-grid">
          <div class="jw crit">
            <span class="k">pass</span>
            <span class="wm">
              <b>硬门槛全过 且 total_score ≥ {{ passLine }}</b>
              <em>规则 / 契约任一未命中即 false</em>
            </span>
          </div>
          <div class="jw crit">
            <span class="k">总分</span>
            <span class="wm">
              <b>数据 40 + 逻辑 30 + 目标 20 + 语气 10 = 100</b>
              <em>维度分即权重，直接加总</em>
            </span>
          </div>
          <div class="jw crit">
            <span class="k">归因</span>
            <span class="wm">
              <b>轮次级：数值错误/数据幻觉 · 口径遗漏 · 上下文指代丢失 · 系统异常　|　会话级：未主动反问 · 超出最大轮次</b>
              <em>只标注问题类型，不参与计分；会话级按整通会话判定</em>
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.judge2 {
  --brand: #4f46e5;
  --brand-deep: #4338ca;
  --brand-soft: #eef2ff;
  --brand-edge: #c7d2fe;
  --line: #e8e8ed;
  --line-soft: #f1f2f6;
  --ink: #111827;
  --ink-soft: #374151;
  --muted: #6b7280;
  --faint: #9ca3af;
  --green: #10b981;
  --green-soft: #ecfdf5;
  --green-edge: #a7f3d0;
  --amber: #d97706;
  --amber-soft: #fffbeb;
  --amber-edge: #fde68a;
  --rose: #e11d48;
  --rose-soft: #fff1f2;
  --rose-edge: #fecdd3;
  margin: 14px 16px 18px;
  color: var(--ink-soft);
  font-size: 11px;
}

.stat-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 9.5px;
  border-radius: 999px;
  padding: 1.5px 7px;
  border: 1px solid var(--line);
  background: #fff;
  color: var(--muted);
  white-space: nowrap;
}
.stat-pill.pass { background: var(--green-soft); border-color: var(--green-edge); color: #047857; }
.stat-pill.warn { background: var(--amber-soft); border-color: var(--amber-edge); color: var(--amber); }
.stat-pill.fail { background: var(--rose-soft); border-color: var(--rose-edge); color: var(--rose); }
.stat-pill.unknown { background: #f8fafc; border-color: #e2e8f0; color: #64748b; }

/* ---------- 概览 ---------- */
.jsum {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 12px 14px;
  background: linear-gradient(180deg, #fff, #fbfbfd);
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.jvcol { display: flex; flex-direction: column; gap: 5px; min-width: 132px; }
.jvcol .row { display: flex; align-items: baseline; gap: 5px; }
.jvcol .sc { font-size: 24px; font-weight: 600; letter-spacing: -0.03em; line-height: 1; color: var(--ink); }
.jvcol .u { font-size: 10px; color: var(--faint); }

.jmid {
  flex: 1;
  min-width: 320px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  border-left: 1px solid var(--line-soft);
  padding-left: 14px;
}
.jdimrow { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 7px; }
.jsd { border: 1px solid var(--line-soft); border-radius: 8px; padding: 5px 8px; background: #fff; min-width: 0; }
.jsd .n { display: block; font-size: 8.5px; color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.jsd .n em { font-style: normal; font-size: 7.5px; color: #b9bfca; margin-left: 3px; }
.jsd .row2 { display: flex; align-items: baseline; gap: 4px; margin-top: 1px; }
.jsd .v { font-family: "JetBrains Mono", Consolas, monospace; font-size: 14px; font-weight: 600; color: var(--ink-soft); line-height: 1.1; }
.jsd .u { font-size: 8px; color: var(--faint); }
.jsd .bar { height: 3px; border-radius: 999px; background: #eef2ff; margin-top: 4px; overflow: hidden; }
.jsd .bar i { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, #a5b4fc, #6366f1); }
.jsd .t { display: block; font-size: 7.5px; color: var(--faint); margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.jsd.low { border-color: var(--amber-edge); background: #fffdf6; }
.jsd.low .v { color: var(--amber); }
.jsd.low .bar { background: #fdecc8; }
.jsd.low .bar i { background: linear-gradient(90deg, #fcd34d, #f59e0b); }
.jsd.low .t { color: #b45309; }

.jtxt { flex: 1; min-width: 260px; font-size: 11px; line-height: 1.75; color: var(--muted); }
.jtxt b { color: var(--ink-soft); font-weight: 600; }
.jtxt > .t { display: block; font-size: 9px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--faint); font-weight: 600; margin-bottom: 3px; }

.jchips { display: flex; gap: 6px; flex-wrap: wrap; align-items: flex-start; max-width: 250px; }
.jchips span { font-size: 9.5px; color: var(--muted); background: #fff; border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; white-space: nowrap; }
.jchips span b { color: var(--ink-soft); font-family: "JetBrains Mono", Consolas, monospace; font-weight: 600; }
.jchips span.passchip { color: #047857; background: var(--green-soft); border-color: var(--green-edge); }
.jchips span.passchip b { color: #047857; }
.jchips span.warnchip { color: #b45309; background: var(--amber-soft); border-color: var(--amber-edge); }
.jchips span.warnchip b { color: #b45309; }
.jchips span.failchip { color: #be123c; background: var(--rose-soft); border-color: var(--rose-edge); }
.jchips span.failchip b { color: #be123c; }

/* ---------- 归因 ---------- */
.jtags { margin-top: 9px; padding-top: 8px; border-top: 1px dashed var(--line); display: flex; flex-direction: column; gap: 5px; }
.jtags-h { font-size: 9px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--faint); font-weight: 600; }
.jtagrow { display: flex; align-items: center; gap: 4px 8px; flex-wrap: wrap; }
.jscope { flex: none; width: 38px; font-size: 9px; color: var(--muted); border-right: 1px solid var(--line-soft); padding-right: 8px; margin-right: 2px; }
.etag { display: inline-flex; align-items: center; gap: 5px; font-size: 9px; line-height: 1.5; color: var(--ink-soft); white-space: nowrap; max-width: 100%; overflow: hidden; text-overflow: ellipsis; }
.etag i { width: 5px; height: 5px; border-radius: 50%; flex: none; background: #cbd5e1; }
.etag.rose i { background: #e11d48; }
.etag.amber i { background: #f59e0b; }
.etag.slate i { background: #94a3b8; }
.etag.sky i { background: #0ea5e9; }
.etag.violet i { background: #8b5cf6; }
.etag.orange i { background: #f97316; }
.etag-none { font-size: 9px; color: #cbd5e1; }
.etag-note { font-size: 9px; color: var(--faint); }
.etag-more { font-size: 8.5px; color: var(--faint); }
.tag-btn { background: none; border: 0; padding: 0; font: inherit; cursor: pointer; text-align: left; }
.tag-btn:hover { color: var(--ink); }
.tag-btn:hover i { box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.18); }
.etag .c { font-family: "JetBrains Mono", Consolas, monospace; font-size: 8px; color: var(--faint); background: #f1f5f9; border-radius: 999px; padding: 0 4px; margin-left: 2px; font-weight: 500; }

/* ---------- 门槛流 ---------- */
.gate-flow { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 7px; }
.gate-flow .g { display: inline-flex; align-items: center; gap: 4px; border: 1px solid var(--line); background: #fff; border-radius: 6px; padding: 2px 7px; font-size: 9px; color: var(--muted); }
.gate-flow .g b { font-family: "JetBrains Mono", Consolas, monospace; color: var(--ink-soft); font-weight: 600; }
.gate-flow .g.bad { border-color: var(--amber-edge); background: var(--amber-soft); color: var(--amber); }
.gate-flow .g.bad b { color: var(--amber); }
.gate-flow .res { border-color: var(--amber-edge); background: var(--amber-soft); color: var(--amber); font-weight: 600; }
.gate-flow .res.pass { border-color: var(--green-edge); background: var(--green-soft); color: #047857; }
.gate-flow .res.fail { border-color: var(--rose-edge); background: var(--rose-soft); color: var(--rose); }
.gate-flow .res.unknown { border-color: var(--line); background: #f8fafc; color: #64748b; }
.gate-flow .arr { color: #cbd5e1; font-size: 9px; }

/* ---------- 未通过门槛 ---------- */
.jfails { border: 1px solid var(--amber-edge); border-radius: 12px; background: linear-gradient(180deg, #fffdf6, #fff); padding: 11px 13px; margin-bottom: 12px; }
.jf-h { display: flex; align-items: center; gap: 8px; margin-bottom: 9px; flex-wrap: wrap; }
.jf-h b { font-size: 10.5px; color: #b45309; }
.jf-h .n { font-family: "JetBrains Mono", Consolas, monospace; font-size: 9px; background: #fef3c7; border-radius: 999px; padding: 1px 6px; color: #b45309; }
.jf-h .sp { flex: 1; }
.jf-h .tip { font-size: 9px; color: #a16207; }
.jf-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 8px; }
.jf { display: flex; align-items: flex-start; gap: 8px; text-align: left; border: 1px solid var(--line); background: #fff; border-radius: 9px; padding: 8px 9px; min-width: 0; transition: border-color 0.15s, box-shadow 0.15s; cursor: pointer; }
.jf:hover { border-color: var(--amber-edge); box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05); }
.jf-k { flex: none; font-size: 8.5px; font-weight: 600; border-radius: 5px; padding: 2px 5px; letter-spacing: 0.02em; line-height: 1.4; }
.jf-k.rule { background: var(--amber-soft); color: #b45309; border: 1px solid var(--amber-edge); }
.jf-k.contract { background: #f1f5f9; color: #334155; border: 1px solid #d8dfe8; }
.jf-main { min-width: 0; flex: 1; }
.jf-main b { display: block; font-size: 10px; color: var(--ink-soft); font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.jf-main em { display: block; font-style: normal; font-size: 9px; color: var(--faint); margin-top: 2px; line-height: 1.5; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.jf-go { flex: none; font-size: 9px; color: var(--brand); align-self: center; display: inline-flex; align-items: center; gap: 1px; }

/* ---------- 矩阵 ---------- */
.jmatrix { border: 1px solid var(--line); border-radius: 11px; background: #fff; overflow: hidden; }
.jm-h { display: flex; align-items: center; gap: 8px; padding: 9px 12px; background: #fbfbfd; border-bottom: 1px solid var(--line-soft); font-size: 9.5px; color: var(--muted); }
.jm-h b { font-size: 10.5px; color: var(--ink-soft); }
.jm-h .sp { flex: 1; }
.jm-h .hint { font-size: 9px; color: var(--faint); }

.heat { width: 100%; border-collapse: collapse; font-size: 10px; }
.heat th { font-size: 8.5px; letter-spacing: 0.04em; color: var(--faint); font-weight: 600; text-align: center; padding: 8px 4px 6px; border-bottom: 1px solid var(--line-soft); white-space: nowrap; }
.heat th.q { text-align: left; padding-left: 12px; width: 30%; }
.heat th.n { text-align: left; padding-left: 10px; width: 38px; }
.heat th.vd { width: 64px; }
.heat th.grp { font-size: 7.5px; letter-spacing: 0.08em; text-transform: uppercase; padding: 5px 4px 3px; border-bottom: 1px solid var(--line-soft); }
.heat th.grp.gate { background: #fffdf5; color: #92400e; }
.heat th.grp.llm { background: #f7f8ff; color: var(--brand-deep); }
.heat th.tags { text-align: left; padding-left: 8px; width: 196px; white-space: nowrap; }
.heat th .dimmax { font-size: 7px; font-weight: 400; color: #b9bfca; margin-left: 3px; }
.heat th.chev, .heat td.chev { width: 26px; padding: 0 8px 0 0; text-align: right; }
.heat td { padding: 5px 4px; border-bottom: 1px solid var(--line-soft); text-align: center; vertical-align: middle; }
.heat tr:last-child td { border-bottom: 0; }
.heat td.gate { background: #fffdf9; padding: 5px 3px; }
.heat td.q { text-align: left; padding: 6px 8px 6px 12px; max-width: 0; color: var(--ink-soft); font-size: 10.5px; line-height: 1.45; }
.heat td.q .qline { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; word-break: break-word; }
.heat td.n { text-align: left; padding-left: 10px; font-family: "JetBrains Mono", Consolas, monospace; font-size: 9.5px; font-weight: 700; color: var(--brand); }
.heat tbody tr.heat-row { cursor: pointer; }
.heat tbody tr.heat-row:hover td { background: #fcfcff; }
.heat tbody tr.heat-row.sel td { background: #f4f5ff; }
.heat td.vd { padding: 5px 2px; }
.heat td.vd .stat-pill { font-size: 8.5px; padding: 1px 5px; }
.heat td.tags { text-align: left; padding-left: 8px; max-width: 196px; }
.etag-cell { display: flex; flex-direction: column; gap: 2px; align-items: flex-start; }
.heat td.chev { color: #cbd5e1; }
.heat td.chev svg { transition: transform 0.15s; }
.heat tr.sel td.chev { color: var(--brand); }
.heat tr.sel td.chev svg { transform: rotate(90deg); }

.cell { display: inline-flex; align-items: center; justify-content: center; min-width: 46px; padding: 3px 6px; border-radius: 6px; border: 1px solid var(--line-soft); background: #f8fafc; color: #475569; font-family: "JetBrains Mono", Consolas, monospace; font-size: 9.5px; font-weight: 600; }
.cell.g { background: var(--green-soft); border-color: var(--green-edge); color: #047857; }
.cell.a { background: var(--amber-soft); border-color: var(--amber-edge); color: var(--amber); }
.cell.r { background: var(--rose-soft); border-color: var(--rose-edge); color: var(--rose); }
.cell.n { background: #f1f5f9; border-color: #e2e8f0; color: #475569; }
.cell.total { min-width: 52px; font-size: 10px; }
.cell.total.pass { background: linear-gradient(180deg, #ecfdf5, #d1fae5); border-color: #6ee7b7; color: #047857; box-shadow: 0 0 0 1px rgba(16, 185, 129, 0.08); }
.cell.total.warn { background: linear-gradient(180deg, #fffbeb, #fef3c7); border-color: #fcd34d; color: #b45309; }
.cell.total.fail { background: linear-gradient(180deg, #fff1f2, #ffe4e6); border-color: #fda4af; color: #be123c; }
.cell.total.unknown { background: #f8fafc; border-color: #e2e8f0; color: #64748b; }

.heat tbody tr.st-pass td:first-child { box-shadow: inset 3px 0 0 #10b981; }
.heat tbody tr.st-warn td:first-child { box-shadow: inset 3px 0 0 #f59e0b; }
.heat tbody tr.st-fail td:first-child { box-shadow: inset 3px 0 0 #f43f5e; }
.heat tbody tr.st-unknown td:first-child { box-shadow: inset 3px 0 0 #cbd5e1; }
.heat tbody tr.st-pass td.n { color: #047857; }
.heat tbody tr.st-warn td.n { color: #b45309; }
.heat tbody tr.st-fail td.n { color: #be123c; }
.heat tbody tr.st-unknown td.n { color: #64748b; }

.heat tr.sum td { border-top: 1px solid var(--line); border-bottom: 0; background: #fbfbfd; font-weight: 600; }
.heat tr.sum td.n { color: var(--ink-soft); font-weight: 500; white-space: nowrap; }
.heat tr.sum td.vd .sumvd { display: inline-flex; align-items: center; gap: 2px; font-size: 9px; color: #047857; background: var(--green-soft); border: 1px solid var(--green-edge); border-radius: 6px; padding: 1px 5px; white-space: nowrap; }
.heat tr.sum td.vd .sumvd b { font-family: "JetBrains Mono", Consolas, monospace; font-weight: 600; }

.heat-legend { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 8px 12px; border-top: 1px solid var(--line-soft); background: #fcfcfe; font-size: 8.5px; color: var(--faint); }
.heat-legend .lg { display: inline-flex; align-items: center; gap: 4px; }
.heat-legend i { display: inline-block; width: 8px; height: 8px; border-radius: 2px; }

/* ---------- 行内展开 ---------- */
.jx > td { padding: 0; background: #fbfbfe; border-bottom: 1px solid var(--line); text-align: left; }
.jx-inner { display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(0, 1fr); gap: 0 20px; padding: 6px 14px 14px 34px; border-left: 2px solid var(--brand); align-items: start; }
.jx-main, .jx-side { min-width: 0; }
.jx-side { display: flex; flex-direction: column; gap: 10px; }
.jx-q { font-size: 11px; line-height: 1.6; color: var(--ink-soft); margin: 0 0 8px; word-break: break-word; }
/* Agent 回复沿用「对话轮次」气泡语言（渐变头像 + 白气泡 + 非对称圆角），裁判理由是独立判定卡 */
.jx-ai { display: grid; grid-template-columns: 26px minmax(0, 1fr); gap: 9px; align-items: start; }
.jx-ai-av { width: 26px; height: 26px; border-radius: 9px; background: linear-gradient(135deg, #818cf8, #4f46e5); color: #fff; display: grid; place-items: center; box-shadow: 0 1px 2px rgba(79, 70, 229, 0.22); }
.jx-ai-bubble { min-width: 0; background: #fff; border: 1px solid #e8e8ed; border-radius: 5px 14px 14px 14px; padding: 9px 14px 11px; box-shadow: 0 1px 2px rgba(17, 24, 39, 0.04); }
.jx-ai-meta { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; font-size: 9.5px; color: #9ca3af; margin-bottom: 6px; }
.jx-ai-meta .sp { flex: 1; }
.jx-ai-meta .who { font-size: 11px; font-weight: 600; color: #374151; }
.jx-ai-meta .tag { font-size: 8.5px; font-weight: 600; color: #4f46e5; background: #eef2ff; border: 1px solid #e0e7ff; border-radius: 999px; padding: 1px 7px; }
.jx-ai-meta .cnt { font-family: "JetBrains Mono", Consolas, monospace; color: #c3c7ce; }
.jx-ai-body { font-size: 12.5px; line-height: 1.9; color: #374151; white-space: pre-wrap; word-break: break-word; overflow-wrap: anywhere; }
.jx-ai-body.clamp { max-height: 143px; overflow: hidden; -webkit-mask-image: linear-gradient(to bottom, #000 76%, rgba(0, 0, 0, 0.05) 100%); mask-image: linear-gradient(to bottom, #000 76%, rgba(0, 0, 0, 0.05) 100%); }
.jx-ai-body.empty { color: var(--faint); font-size: 10.5px; line-height: 1.7; }
.jx-more { display: inline-flex; align-items: center; gap: 3px; margin-top: 5px; padding: 0; border: 0; background: none; font-size: 9.5px; color: var(--brand); cursor: pointer; }
.jx-more:hover { text-decoration: underline; }
.jx-more svg { transition: transform 0.15s; }
.jx-more svg.flip { transform: rotate(180deg); }
.jx-judge { margin-top: 10px; border: 1px solid var(--brand-edge); border-left: 3px solid var(--brand); border-radius: 8px; background: #fcfcff; padding: 8px 12px 10px; }
.jx-judge-h { display: flex; align-items: center; gap: 6px; font-size: 8.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--brand-deep); font-weight: 600; }
.jx-judge-h .av { flex: none; display: inline-flex; align-items: center; justify-content: center; width: 17px; height: 17px; border-radius: 5px; background: var(--brand-soft); border: 1px solid var(--brand-edge); color: var(--brand-deep); }
.jx-judge-h b { font-size: 9.5px; font-weight: 600; letter-spacing: 0.06em; color: inherit; }
.jx-judge-h .note { font-size: 8.5px; letter-spacing: 0; text-transform: none; font-weight: 400; color: var(--faint); }
.jx-judge p { margin-top: 7px; font-size: 12px; line-height: 1.9; color: var(--ink-soft); }
.jx-judge p.clamp { max-height: 137px; overflow: hidden; -webkit-mask-image: linear-gradient(to bottom, #000 74%, rgba(0, 0, 0, 0.05) 100%); mask-image: linear-gradient(to bottom, #000 74%, rgba(0, 0, 0, 0.05) 100%); }
.jx-judge p.jd-conc-note { color: var(--faint); font-size: 9.5px; line-height: 1.6; margin-top: 5px; }
.jd-dims { margin-top: 10px; border: 1px solid var(--line-soft); border-radius: 8px; background: #fbfbfd; padding: 8px 11px 10px; }
.jx-sec-h { display: flex; align-items: center; gap: 5px; font-size: 8.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); font-weight: 600; }
.jx-sec-h b { font-size: 9px; font-weight: 600; letter-spacing: 0.06em; color: var(--ink-soft); }
.jx-sec-h .note { font-size: 8.5px; letter-spacing: 0; text-transform: none; font-weight: 400; color: var(--faint); }
.jd-dims-rows { margin-top: 7px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 16px; }
.jd-dim { display: grid; grid-template-columns: 68px minmax(0, 1fr) 28px; gap: 7px; align-items: center; font-size: 9px; color: var(--muted); }
.jd-dim .lb { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.jd-dim .bar { height: 5px; border-radius: 999px; background: #f1f5f9; overflow: hidden; }
.jd-dim .bar i { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, #a5b4fc, #6366f1); }
.jd-dim .bar i.low { background: linear-gradient(90deg, #fcd34d, #f59e0b); }
.jd-dim b { font-family: "JetBrains Mono", Consolas, monospace; font-size: 9.5px; color: var(--ink-soft); text-align: right; }
.jd-dim b.low { color: var(--amber); }
.jd-dim b small { font-size: 7.5px; color: var(--faint); font-weight: 400; }
.jd-dims-empty { margin-top: 6px; font-size: 10px; color: var(--faint); line-height: 1.6; }
.jx-attrib { border: 1px solid var(--line); border-radius: 8px; background: #fff; padding: 7px 9px 8px; }
.jx-attrib-row { display: flex; align-items: center; gap: 5px 6px; flex-wrap: wrap; margin-top: 6px; }
.jx-attrib-row > b { flex: none; width: 34px; font-size: 8.5px; letter-spacing: 0.06em; color: var(--faint); font-weight: 600; }

.jx-acts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
.jx-acts button { font-size: 9.5px; border: 1px solid var(--line); border-radius: 6px; padding: 5px 8px; color: var(--muted); background: #fff; display: flex; align-items: center; justify-content: center; gap: 4px; white-space: nowrap; cursor: pointer; }
.jx-acts button:hover { border-color: var(--brand-edge); color: var(--brand); }
.jx-acts .primary { background: var(--brand-soft); border-color: var(--brand-edge); color: var(--brand-deep); }

/* ---------- 门槛检查 ---------- */
.jd-checks { margin-top: 0; border: 1px solid var(--line); border-radius: 8px; background: #fff; padding: 7px 9px; }
.jdc-h { display: flex; align-items: center; gap: 6px; font-size: 8.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--faint); font-weight: 600; margin-bottom: 4px; }
.jdc-h .sp { flex: 1; }
.jdc-h b { letter-spacing: 0; font-size: 9px; font-weight: 600; }
.jdc-h b.bad { color: var(--amber); }
.jdc-h b.ok { color: var(--green); }
.jdc { display: flex; align-items: flex-start; gap: 5px; font-size: 9.5px; line-height: 1.5; padding: 2px 0; color: var(--muted); }
.jdc .ic { flex: none; width: 11px; height: 11px; margin-top: 1px; display: inline-flex; }
.jdc.ok .ic { color: var(--green); }
.jdc.bad .ic { color: var(--amber); }
.jdc.bad .nm { color: var(--ink-soft); font-weight: 600; }
.jdc .why { margin-left: auto; flex: none; max-width: 50%; text-align: right; color: var(--amber); font-size: 8.5px; line-height: 1.45; }
.jdc-sep { margin-top: 5px; padding-top: 5px; border-top: 1px dashed var(--line); font-size: 8.5px; color: var(--faint); font-weight: 600; letter-spacing: 0.06em; }
.jdc-none { margin-top: 4px; font-size: 8.5px; color: var(--faint); line-height: 1.5; }

/* ---------- 判定口径 ---------- */
.jweak { margin-top: 12px; border: 1px solid var(--line); border-radius: 11px; background: #fff; padding: 10px 12px; }
.jw-h { display: flex; align-items: center; gap: 8px; font-size: 9.5px; color: var(--muted); margin-bottom: 8px; }
.jw-h b { font-size: 10.5px; color: var(--ink-soft); }
.jw-h .sp { flex: 1; }
.jw-h .tip { font-size: 9px; color: var(--faint); }
.jw-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.jw { display: flex; align-items: center; gap: 8px; border: 1px solid var(--line-soft); border-radius: 8px; padding: 7px 9px; background: #fffdf9; min-width: 0; text-align: left; }
.jw.crit { background: #fcfcfe; cursor: default; align-items: flex-start; }
.jw.crit .k { flex: none; font-size: 8.5px; color: var(--muted); background: #eef2ff; border: 1px solid var(--line); border-radius: 5px; padding: 2px 5px; margin-top: 1px; white-space: nowrap; }
.jw .wm { min-width: 0; }
.jw b { display: block; font-size: 9.5px; color: var(--ink-soft); font-weight: 600; white-space: normal; }
.jw em { display: block; font-style: normal; font-size: 8.5px; color: var(--faint); margin-top: 2px; line-height: 1.45; white-space: normal; }

@media (max-width: 1100px) {
  .jsum .jchips { max-width: none; }
  .jx-inner { grid-template-columns: minmax(0, 1fr) 250px; }
  .jw-grid { grid-template-columns: 1fr; }
}
/* 窄容器放不下 12 列：隐藏「提问」列，问题正文在行内展开里完整可读 */
@media (max-width: 960px) {
  .heat th.q,
  .heat td.q { display: none; }
  .heat th.tags,
  .heat td.tags { width: 150px; max-width: 150px; }
  .heat td.q .qline { -webkit-line-clamp: 3; }
}
@media (max-width: 820px) {
  .jdimrow { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .jx-inner { grid-template-columns: 1fr; }
  .jd-dims-rows { grid-template-columns: 1fr; }
  .jmatrix { overflow-x: auto; }
}
</style>

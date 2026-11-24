<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import EmptyState from '../EmptyState.vue'
import StatusPill from '../StatusPill.vue'
import ExpectedFactTable from './ExpectedFactTable.vue'
import ExpectedRuleTable from './ExpectedRuleTable.vue'
import type { ExecutionDetail, TurnTrace } from '../../types'
import {
  factRowsForDisplay,
  resolveExpectation,
  type FactRow,
  type RuleRow,
  type TurnExpectation,
} from '../../utils/expectation'
import {
  diffGlyph,
  diffText,
  entityDiff,
  visibleDiffRows,
  type DiffResult,
  type DiffRow,
} from '../../utils/textDiff'
import { statusLabel } from '../../utils/format'

const props = defineProps<{
  detail: ExecutionDetail
  onlyDiff: boolean
  syncScroll: boolean
  keyOnly: boolean
}>()

const resolved = computed(() => resolveExpectation(props.detail))
const isStatic = computed(() => resolved.value.caseType === 'STATIC_MULTI')
const isDynamic = computed(() => resolved.value.caseType === 'DYNAMIC_MULTI')
const overallStatus = computed(() => {
  if (props.detail.pass_flag === true) return 'PASS'
  if (props.detail.pass_flag === false) return 'FAIL'
  return props.detail.status
})

const facts = computed<FactRow[]>(() => factRowsForDisplay(resolved.value.facts))
const factPassed = computed(() => facts.value.filter((fact) => fact.passed === true).length)
const factMeasured = computed(() => facts.value.filter((fact) => fact.passed !== null).length)
const factSummary = computed(() =>
  factMeasured.value ? `${factPassed.value}/${factMeasured.value}` : '—',
)
const factSectionLabel = computed(() =>
  facts.value.some((fact) => fact.section === 'must') ? 'Must' : 'May',
)

function emptyDiff(): DiffResult {
  return { rows: [], changes: 0, extras: 0, missing: 0, hasDiff: false }
}

function rowsToDiff(rows: DiffRow[]): DiffResult {
  let changes = 0
  let extras = 0
  let missing = 0
  rows.forEach((row) => {
    if (row.kind === 'change') changes += 1
    else if (row.kind === 'extra') extras += 1
    else if (row.kind === 'missing') missing += 1
  })
  return { rows, changes, extras, missing, hasDiff: changes + extras + missing > 0 }
}

function diffCount(result: DiffResult): number {
  return result.changes + result.extras + result.missing
}

function ruleRowsToDiff(rows: RuleRow[]): DiffRow[] {
  return rows.map((row) => {
    const leftText = `${row.label} ${row.expectedText}`.trim()
    const rightText = `${row.label} ${row.actualText || '—'}`.trim()
    if (row.passed === false) {
      return {
        kind: 'change',
        left: [{ text: leftText, kind: 'plain' }],
        right: [{ text: rightText, kind: 'insert' }],
      }
    }
    return {
      kind: 'same',
      left: [{ text: leftText, kind: 'plain' }],
      right: [{ text: row.passed === null ? `${rightText}（未校验）` : rightText, kind: 'plain' }],
    }
  })
}

/* ---------------------------------------------------------- 单轮 */
const singleTurn = computed<TurnExpectation | null>(() => resolved.value.turns[0] ?? null)
const singleDiff = computed<DiffResult>(() => {
  const turn = singleTurn.value
  if (!turn) return emptyDiff()
  if (turn.expectedText) return diffText(turn.expectedText, turn.answer)
  return rowsToDiff(ruleRowsToDiff(turn.ruleRows))
})
const singleRows = computed(() => visibleDiffRows(singleDiff.value.rows, props.onlyDiff))
const singleChanges = computed(() => diffCount(singleDiff.value))

/* ---------------------------------------------------------- 静态多轮 */
const activeTurnIndex = ref<number | null>(null)

function turnState(turn: TurnExpectation): 'pass' | 'fail' | 'unknown' {
  if (turn.passed === false || turn.ruleRows.some((row) => row.passed === false)) {
    return 'fail'
  }
  if (
    turn.passed === true ||
    (turn.ruleRows.length > 0 && turn.ruleRows.every((row) => row.passed === true))
  ) {
    return 'pass'
  }
  return 'unknown'
}

function turnStatusLabel(turn: TurnExpectation): string {
  const state = turnState(turn)
  if (state === 'pass') return '通过'
  if (state === 'fail') return '未通过'
  return '待判定'
}

function turnStatus(turn: TurnExpectation): string {
  const state = turnState(turn)
  if (state === 'pass') return 'PASS'
  if (state === 'fail') return 'FAIL'
  return 'PENDING'
}

function turnRuleSummary(turn: TurnExpectation): string {
  const measured = turn.ruleRows.filter((row) => row.passed !== null)
  if (!measured.length) return '无规则'
  return `${measured.filter((row) => row.passed === true).length}/${measured.length}`
}

function turnDiff(turn: TurnExpectation): DiffResult {
  if (turn.expectedText) return diffText(turn.expectedText, turn.answer)
  return rowsToDiff(ruleRowsToDiff(turn.ruleRows))
}

function turnRows(turn: TurnExpectation): DiffRow[] {
  return visibleDiffRows(turnDiff(turn).rows, props.onlyDiff)
}

function isActive(turn: TurnExpectation): boolean {
  return activeTurnIndex.value === turn.turnIndex
}

function toggleTurn(turn: TurnExpectation): void {
  activeTurnIndex.value = isActive(turn) ? null : turn.turnIndex
}

function collapsedExpectation(turn: TurnExpectation): string {
  const text =
    turn.expectedText ||
    turn.ruleRows
      .slice(0, 3)
      .map((row) => `${row.label} ${row.expectedText}`)
      .join(' · ')
  if (!text) return '未配置期望'
  return text.length > 78 ? `${text.slice(0, 78)}…` : text
}

const staticPassed = computed(
  () => resolved.value.turns.filter((turn) => turnState(turn) === 'pass').length,
)
const staticFailed = computed(
  () => resolved.value.turns.filter((turn) => turnState(turn) === 'fail').length,
)

const session = computed(() => resolved.value.session)
const sessionSummary = computed(() => {
  const rows = session.value?.ruleRows ?? []
  const measured = rows.filter((row) => row.passed !== null)
  if (!measured.length) return ''
  return `${measured.filter((row) => row.passed === true).length}/${measured.length}`
})

watch(
  () => props.detail.id,
  () => {
    const failing = resolved.value.turns.find((turn) => turnState(turn) === 'fail')
    activeTurnIndex.value =
      failing?.turnIndex ?? resolved.value.turns[resolved.value.turns.length - 1]?.turnIndex ?? null
  },
  { immediate: true },
)

/* ---------------------------------------------------------- 动态多轮 */
const dynamic = computed(() => resolved.value.dynamic)
const hiddenRows = computed(() => dynamic.value?.hidden ?? [])
const releasedCount = computed(
  () => hiddenRows.value.filter((row) => row.releasedTurn !== null).length,
)
const missingHidden = computed(() =>
  hiddenRows.value.filter((row) => row.releasedTurn === null),
)

const termination = computed(
  () => props.detail.termination_status ?? props.detail.status,
)
const terminationText = computed(() => statusLabel(termination.value))
const turnsUsed = computed(
  () => props.detail.turns_used || (props.detail.turns?.length ?? 0),
)
const maxTurns = computed(() => dynamic.value?.maxTurns ?? null)

interface TimelineItem {
  turn: TurnTrace
  released: Array<{ key: string; content: string; releaseHint: string }>
  warning: string | null
  key: boolean
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

const timeline = computed<TimelineItem[]>(() => {
  const hidden = hiddenRows.value
  const turns = props.detail.turns ?? []
  const releasedKeys = new Set<string>()
  const warnedKeys = new Set<string>()
  return turns.map((turn) => {
    const raw = Array.isArray(turn.hidden_context_released)
      ? turn.hidden_context_released
      : []
    const released = raw.filter(isRecord).map((item) => ({
      key: String(item.key ?? ''),
      content: String(item.content ?? ''),
      releaseHint: String(item.release_hint ?? ''),
    }))
    const releasedNow = new Set(released.map((item) => item.key))
    const pending = hidden.filter(
      (item) => !releasedKeys.has(item.key) && !releasedNow.has(item.key),
    )
    let warning: string | null = null
    if (turn.final_answer && pending.length && /[¥\d]/.test(turn.final_answer)) {
      const fresh = pending.filter((item) => !warnedKeys.has(item.key))
      if (fresh.length) {
        warning = `未澄清${fresh
          .map((item) => item.releaseHint || item.key)
          .join('、')}即作答`
        fresh.forEach((item) => warnedKeys.add(item.key))
      }
    }
    released.forEach((item) => releasedKeys.add(item.key))
    const key =
      released.length > 0 ||
      (turn.simulator_status !== null && turn.simulator_status !== 'CONTINUE') ||
      Boolean(warning)
    return { turn, released, warning, key }
  })
})

const visibleTimeline = computed(() => {
  const list = timeline.value
  if (!props.keyOnly) return list
  return list.filter(
    (item, index) => index === 0 || index === list.length - 1 || item.key,
  )
})

const finalTurn = computed<TurnTrace | null>(() => {
  const turns = props.detail.turns ?? []
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    if (turns[index].final_answer) return turns[index]
  }
  return turns[turns.length - 1] ?? null
})

const finalDiff = computed<DiffResult>(() => {
  const expected = dynamic.value?.expectedAnswer ?? null
  if (!expected) return emptyDiff()
  const actual = finalTurn.value?.final_answer ?? null
  const rules = dynamic.value?.rules ?? []
  return (
    entityDiff(expected, actual ?? '', {
      amountLabel: rules.find((rule) => /金额|总额|费|余额|价/.test(rule.label))?.label,
      countLabel: rules.find((rule) => /项数|笔数|数量|次数/.test(rule.label))?.label,
    }) ?? diffText(expected, actual)
  )
})
const finalRows = computed(() => visibleDiffRows(finalDiff.value.rows, false))

const dynamicRules = computed(() => dynamic.value?.rules ?? [])

function timelineTone(item: TimelineItem): string {
  const status = item.turn.simulator_status ?? ''
  if (
    item.warning ||
    status === 'GIVE_UP' ||
    status === 'MAX_TURNS_EXCEEDED' ||
    status === 'REPETITION_ABORT'
  ) {
    return 'fail'
  }
  return 'ok'
}

function simulatorTone(status: string | null): string {
  if (!status) return 'gray'
  if (status === 'GOAL_ACHIEVED') return 'ok'
  if (status === 'GIVE_UP' || status === 'REPETITION_ABORT') return 'no'
  return 'gray'
}

/* ---------------------------------------------------------- 共用展示 */
function segmentClass(kind: string, side: 'left' | 'right', rowKind?: string): string {
  if (kind === 'delete') return 'seg-expect'
  if (kind === 'insert') {
    if (rowKind === 'extra') return 'seg-extra'
    return side === 'right' ? 'seg-bad' : 'seg-expect'
  }
  return ''
}

function summaryClass(): string {
  if (props.detail.pass_flag === true) return 'summary-pass'
  if (props.detail.pass_flag === false) return 'summary-fail'
  return ''
}
</script>

<template>
  <div class="space-y-3">
    <EmptyState
      v-if="!resolved.hasExpectation"
      compact
      title="没有可对照的期望答案"
      description="该执行快照未配置期望答案、判分事实或规则。可前往用例详情补充后再执行。"
    />

    <!-- ============================ C1 单轮 ============================ -->
    <template v-else-if="!isStatic && !isDynamic">
      <div class="summary-bar" :class="summaryClass()">
        <StatusPill :status="overallStatus" />
        <span><b>{{ singleChanges }}</b> 处差异</span>
        <span class="sep">·</span>
        <span>{{ singleDiff.changes }} 处文本/数值不一致</span>
        <span v-if="singleDiff.extras" class="sep">·</span>
        <span v-if="singleDiff.extras" class="warn-text">{{ singleDiff.extras }} 处扩展内容</span>
        <span v-if="facts.length" class="ml-auto fact-chip">{{ factSectionLabel }} {{ factSummary }} 命中</span>
      </div>

      <div v-if="singleTurn && !singleTurn.expectedText && singleTurn.ruleRows.length" class="notice-box">
        <span aria-hidden="true">ⓘ</span>
        <span>该用例未填写自然语言期望答案，以下按判分规则生成对照。</span>
        <span class="gen-badge">规则生成</span>
      </div>

      <div class="diff-wrap">
        <div class="diff-head">
          <div class="gutter"></div>
          <div>期望答案 <span class="src">· 用例快照 v{{ resolved.version }}</span></div>
          <div>实际回复 <span class="src">· 执行 #{{ detail.id }}</span></div>
        </div>
        <div v-if="singleRows.length" class="diff-body" :class="{ 'diff-scroll': syncScroll }">
          <div
            v-for="(row, index) in singleRows"
            :key="index"
            class="diff-line"
            :class="row.kind"
          >
            <div class="gutter">{{ diffGlyph(row.kind) }}</div>
            <div class="diff-cell">
              <span v-for="(segment, segmentIndex) in row.left" :key="segmentIndex" :class="segmentClass(segment.kind, 'left', row.kind)">{{ segment.text }}</span>
              <span v-if="!row.left.length" class="muted">—</span>
            </div>
            <div class="diff-cell">
              <span v-for="(segment, segmentIndex) in row.right" :key="segmentIndex" :class="segmentClass(segment.kind, 'right', row.kind)">{{ segment.text }}</span>
              <span v-if="!row.right.length" class="muted">—</span>
            </div>
          </div>
        </div>
        <EmptyState v-else compact title="没有差异" description="期望答案与实际回复一致。" />
        <div class="diff-foot">
          <span class="legend"><i class="legend-same"></i>一致</span>
          <span class="legend"><i class="legend-change"></i>不一致</span>
          <span class="legend"><i class="legend-extra"></i>期望之外的扩展</span>
          <span class="spacer"></span>
          <span>判分依据：{{ facts.length ? 'Must 事实' : '判分规则' }}</span>
        </div>
      </div>

      <ExpectedRuleTable
        v-if="singleTurn?.ruleRows.length"
        class="mt-3"
        :rows="singleTurn.ruleRows"
        title="规则校验"
      />
      <ExpectedFactTable class="mt-3" :facts="facts" />
    </template>

    <!-- ========================== C2 静态多轮 ========================== -->
    <template v-else-if="isStatic">
      <div class="turn-rail">
        <button
          v-for="turn in resolved.turns"
          :key="turn.turnIndex"
          type="button"
          class="turn-pill"
          :class="{ active: isActive(turn) }"
          @click="toggleTurn(turn)"
        >
          <span class="st" :class="turnState(turn)"></span>
          T{{ turn.turnIndex }} {{ turnStatusLabel(turn) }}
          <span class="mini-count">{{ turnRuleSummary(turn) }}</span>
        </button>
        <span class="rail-hint">
          {{ resolved.turns.length }} 轮 · {{ staticPassed }} 通过 /
          {{ staticFailed }} 未通过<span v-if="sessionSummary"> · 会话级 {{ sessionSummary }}</span>
        </span>
      </div>

      <div v-for="turn in resolved.turns" :key="`turn-${turn.turnIndex}`">
        <button
          v-if="!isActive(turn)"
          type="button"
          class="collapsed-turn"
          @click="toggleTurn(turn)"
        >
          <span class="tag">T{{ turn.turnIndex }}</span>
          <span class="collapsed-q">{{ turn.query }}</span>
          <span class="collapsed-sep">·</span>
          <b class="collapsed-exp">{{ collapsedExpectation(turn) }}</b>
          <span class="tag">{{ turnRuleSummary(turn) }}</span>
          <span class="expand-link">展开对照 ↓</span>
        </button>

        <div v-else class="space-y-2.5">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="flex min-w-0 flex-wrap items-center gap-2">
              <span class="turn-id">T{{ turn.turnIndex }}</span>
              <StatusPill :status="turnStatus(turn)" :label="turnStatusLabel(turn)" size="sm" />
              <span class="truncate text-[11px] text-slate-500">{{ turn.query }}</span>
            </div>
            <button class="collapse-btn" type="button" @click="toggleTurn(turn)">收起</button>
          </div>

          <div class="summary-bar" :class="turnState(turn) === 'fail' ? 'summary-fail' : turnState(turn) === 'pass' ? 'summary-pass' : ''">
            <span class="font-medium text-slate-700">T{{ turn.turnIndex }}</span>
            <span><b>{{ diffCount(turnDiff(turn)) }}</b> 处差异</span>
            <span class="sep">·</span>
            <span>规则 {{ turnRuleSummary(turn) }}</span>
            <span v-if="turn.expectedSource === 'RULES'" class="gen-badge">规则生成</span>
            <span class="ml-auto fact-chip">
              {{ turn.ruleRows.filter((row) => row.passed === false).length }} 条规则未通过
            </span>
          </div>

          <div v-if="!turn.expectedText && turn.ruleRows.length" class="notice-box">
            <span aria-hidden="true">ⓘ</span>
            <span>该轮未填写自然语言期望答案，以下按轮次 expected_rules 生成对照。</span>
            <span class="gen-badge">规则生成</span>
          </div>

          <div class="diff-wrap">
            <div class="diff-head">
              <div class="gutter"></div>
              <div>
                期望 · T{{ turn.turnIndex }}
                <span class="src">· 用例快照 v{{ resolved.version }}</span>
                <span v-if="turn.expectedSource === 'RULES'" class="gen-badge ml-1">规则生成</span>
              </div>
              <div>实际 · T{{ turn.turnIndex }} <span class="src">· 执行 #{{ detail.id }}</span></div>
            </div>
            <div v-if="turnRows(turn).length" class="diff-body" :class="{ 'diff-scroll': syncScroll }">
              <div
                v-for="(row, index) in turnRows(turn)"
                :key="index"
                class="diff-line"
                :class="row.kind"
              >
                <div class="gutter">{{ diffGlyph(row.kind) }}</div>
                <div class="diff-cell">
                  <span v-for="(segment, segmentIndex) in row.left" :key="segmentIndex" :class="segmentClass(segment.kind, 'left', row.kind)">{{ segment.text }}</span>
                  <span v-if="!row.left.length" class="muted">—</span>
                </div>
                <div class="diff-cell">
                  <span v-for="(segment, segmentIndex) in row.right" :key="segmentIndex" :class="segmentClass(segment.kind, 'right', row.kind)">{{ segment.text }}</span>
                  <span v-if="!row.right.length" class="muted">—</span>
                </div>
              </div>
            </div>
            <EmptyState v-else compact title="没有差异" description="该轮期望与实际一致。" />
            <div class="diff-foot">
              <span class="legend"><i class="legend-same"></i>一致</span>
              <span class="legend"><i class="legend-change"></i>不一致</span>
              <span class="spacer"></span>
              <span>{{ turn.ruleRows.length ? '判分依据：轮次规则' : '判分依据：期望答案' }}</span>
            </div>
          </div>

          <ExpectedRuleTable
            v-if="turn.ruleRows.length"
            :rows="turn.ruleRows"
            :title="`轮次规则校验 · T${turn.turnIndex}`"
          />
        </div>
      </div>

      <div v-if="session" class="session-expect">
        <div class="flex flex-wrap items-center gap-2">
          <span class="session-tag">会话级期望</span>
          <span class="text-[10px] text-slate-500">session_expected · 跨轮汇总</span>
        </div>
        <p v-if="session.text" class="session-text">{{ session.text }}</p>
        <div v-if="session.ruleRows.length" class="mt-2 flex flex-wrap gap-1.5">
          <span
            v-for="row in session.ruleRows"
            :key="row.id"
            class="fact-mini"
            :class="row.passed === false ? 'no' : row.passed === true ? 'ok' : ''"
          >
            {{ row.label }} {{ row.expectedText }}
            <template v-if="row.passed === false">· 未命中</template>
            <template v-else-if="row.passed === true">· 命中</template>
          </span>
        </div>
      </div>

      <ExpectedFactTable v-if="facts.length" :facts="facts" />
    </template>

    <!-- ========================== C3 动态多轮 ========================== -->
    <template v-else>
      <div class="summary-bar" :class="summaryClass()">
        <StatusPill :status="overallStatus" />
        <span class="font-medium text-slate-700">{{ terminationText }}</span>
        <span class="sep">·</span>
        <span :class="missingHidden.length ? 'bad-text' : 'good-text'">
          隐藏信息 {{ releasedCount }}/{{ hiddenRows.length }} 已释放
        </span>
        <span v-if="missingHidden.length" class="sep">·</span>
        <span v-if="missingHidden.length" class="bad-text">
          未释放：{{ missingHidden.map((row) => row.key).join('、') }}
        </span>
        <span class="sep">·</span>
        <span>轮次 {{ turnsUsed }}/{{ maxTurns ?? '—' }}</span>
        <span
          v-if="detail.termination_status === 'GOAL_ACHIEVED' && detail.pass_flag === false"
          class="target-warn"
        >
          目标达成但判分未通过
        </span>
        <span v-if="facts.length" class="ml-auto fact-chip">{{ factSectionLabel }} {{ factSummary }} 命中</span>
      </div>

      <div class="dynamic-grid">
        <section class="plan-card">
          <div class="panel-strip">
            测试设计（期望）
            <span class="panel-sub">用例快照 v{{ resolved.version }} · 执行时冻结</span>
          </div>
          <div class="plan-body">
            <div v-if="dynamic?.goal" class="plan-block">
              <p class="panel-label">用户目标</p>
              <p class="goal-text">{{ dynamic.goal }}</p>
              <p v-if="dynamic.persona" class="goal-meta">人设：{{ dynamic.persona }}</p>
            </div>
            <div v-if="dynamic?.initialQuery" class="plan-block">
              <p class="panel-label">初始提问</p>
              <p class="plan-quote">{{ dynamic.initialQuery }}</p>
            </div>
            <div v-if="hiddenRows.length" class="plan-block">
              <p class="panel-label">隐藏信息释放计划</p>
              <table class="hidden-table">
                <thead>
                  <tr>
                    <th>信息</th>
                    <th>值</th>
                    <th>释放时机</th>
                    <th>实际</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in hiddenRows" :key="row.key">
                    <td class="mono text-[10px] text-indigo-600">{{ row.key }}</td>
                    <td>{{ row.content }}</td>
                    <td>{{ row.releaseHint || '—' }}</td>
                    <td>
                      <span class="release" :class="row.releasedTurn !== null ? 'ok' : 'no'">
                        {{ row.releasedTurn !== null ? `✓ T${row.releasedTurn} 已释放` : '✕ 未释放' }}
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="plan-block">
              <p class="panel-label">终止与熔断</p>
              <div class="plan-meta">
                <span v-for="keyword in dynamic?.terminateKeywords ?? []" :key="keyword" class="chip-mini">
                  关键词 {{ keyword }}
                </span>
                <span class="chip-mini">最大轮次 {{ maxTurns ?? '—' }}</span>
                <span class="chip-mini">实际 {{ turnsUsed }}/{{ maxTurns ?? '—' }}</span>
                <StatusPill :status="termination" size="sm" class="ml-auto" />
              </div>
            </div>
            <div v-if="dynamic?.expectedAnswer" class="plan-block">
              <p class="panel-label">期望最终答案</p>
              <p class="plan-quote">{{ dynamic.expectedAnswer }}</p>
              <div v-if="dynamicRules.length && resolved.gradingMode !== 'ANSWER_CONTRACT'" class="plan-meta">
                <span v-for="rule in dynamicRules.slice(0, 4)" :key="rule.id" class="tag">
                  {{ rule.label }} {{ rule.expectedText }}
                </span>
              </div>
            </div>
          </div>
        </section>

        <section class="timeline-card">
          <div class="panel-strip">
            执行轨迹（实际）
            <span class="panel-sub">{{ detail.turns.length }} 轮 · {{ terminationText }}</span>
          </div>
          <div class="timeline">
            <div
              v-for="item in visibleTimeline"
              :key="item.turn.id"
              class="tl-item"
              :class="timelineTone(item)"
            >
              <span class="tl-dot"></span>
              <div class="tl-head">
                <span class="tl-id">T{{ item.turn.turn_index }}</span>
                <span
                  v-if="item.turn.simulator_status"
                  class="tl-event"
                  :class="simulatorTone(item.turn.simulator_status)"
                >
                  {{ item.turn.simulator_status }}
                </span>
                <span v-for="released in item.released" :key="released.key" class="tl-event ok">
                  释放 {{ released.key }}
                </span>
                <span v-if="item.warning" class="tl-event warn">{{ item.warning }}</span>
              </div>
              <div class="tl-q"><span class="tl-label">用户</span>{{ item.turn.query }}</div>
              <div class="tl-a" :class="{ 'tl-a-bad': Boolean(item.warning) }">
                <span class="tl-label">Agent</span>{{ item.turn.final_answer || '—' }}
              </div>
              <p v-if="item.warning" class="tl-hint">{{ item.warning }}</p>
            </div>
          </div>
        </section>
      </div>

      <div v-if="dynamic?.expectedAnswer" class="diff-wrap">
        <div class="diff-head">
          <div class="gutter"></div>
          <div>最终答案 · 期望 <span class="src">· 用例快照 v{{ resolved.version }}</span></div>
          <div>
            最终答案 · 实际
            <span class="src">· T{{ finalTurn?.turn_index ?? '—' }} · 执行 #{{ detail.id }}</span>
          </div>
        </div>
        <div v-if="finalRows.length" class="diff-body">
          <div
            v-for="(row, index) in finalRows"
            :key="index"
            class="diff-line"
            :class="row.kind"
          >
            <div class="gutter">{{ diffGlyph(row.kind) }}</div>
            <div class="diff-cell">
              <span v-for="(segment, segmentIndex) in row.left" :key="segmentIndex" :class="segmentClass(segment.kind, 'left', row.kind)">{{ segment.text }}</span>
              <span v-if="!row.left.length" class="muted">—</span>
            </div>
            <div class="diff-cell">
              <span v-for="(segment, segmentIndex) in row.right" :key="segmentIndex" :class="segmentClass(segment.kind, 'right', row.kind)">{{ segment.text }}</span>
              <span v-if="!row.right.length" class="muted">—</span>
            </div>
          </div>
        </div>
        <EmptyState v-else compact title="最终答案一致" description="期望与实际最终答案没有差异。" />
        <div class="diff-foot">
          <span class="legend"><i class="legend-same"></i>一致</span>
          <span class="legend"><i class="legend-change"></i>不一致</span>
          <span class="legend"><i class="legend-extra"></i>期望之外的扩展</span>
          <span class="spacer"></span>
          <span>动态多轮以最后一轮结论为准</span>
        </div>
      </div>
      <div v-else class="notice-box">
        <span aria-hidden="true">ⓘ</span>
        <span>该用例未配置期望最终答案，以下仅展示执行轨迹与判分事实。</span>
      </div>

      <ExpectedFactTable v-if="facts.length" :facts="facts" />
    </template>
  </div>
</template>

<style scoped>
.summary-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  border: 1px solid #e8e8ed;
  background: #f8fafc;
  border-radius: 12px;
  padding: 7px 10px;
  font-size: 11px;
  color: #475569;
}
.summary-fail {
  border-color: #fecdd3;
  background: #fff7f8;
}
.summary-pass {
  border-color: #a7f3d0;
  background: #f7fefb;
}
.summary-bar b {
  color: #be123c;
}
.summary-pass b {
  color: #047857;
}
.sep {
  color: #cbd5e1;
}
.bad-text {
  color: #be123c;
  font-weight: 500;
}
.good-text {
  color: #047857;
  font-weight: 500;
}
.warn-text {
  color: #92400e;
  font-weight: 500;
}
.fact-chip {
  border: 1px solid #fecdd3;
  background: #fff;
  border-radius: 999px;
  padding: 1px 8px;
  color: #be123c;
  font-size: 10px;
}
.summary-pass .fact-chip {
  border-color: #a7f3d0;
  color: #047857;
}
.target-warn {
  border-radius: 999px;
  border: 1px solid #fde68a;
  background: #fffbeb;
  color: #92400e;
  padding: 1px 8px;
  font-size: 10px;
}

.notice-box {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  border: 1px solid #fde68a;
  background: #fffbeb;
  color: #92400e;
  border-radius: 10px;
  padding: 7px 9px;
  font-size: 10.5px;
  line-height: 1.6;
}
.notice-box > span:nth-child(2) {
  flex: 1;
}
.gen-badge {
  display: inline-flex;
  align-items: center;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  color: #64748b;
  border-radius: 5px;
  padding: 1px 5px;
  font-size: 9.5px;
  white-space: nowrap;
}

.diff-wrap {
  overflow: hidden;
  border: 1px solid #e8e8ed;
  border-radius: 14px;
  background: #fff;
}
.diff-head {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr) minmax(0, 1fr);
  background: #f8fafc;
  border-bottom: 1px solid #e8e8ed;
  font-size: 11px;
  font-weight: 600;
  color: #475569;
}
.diff-head > div {
  padding: 7px 10px;
}
.diff-head > div + div {
  border-left: 1px solid #e8e8ed;
}
.diff-head .gutter {
  padding: 0;
}
.src {
  font-weight: 400;
  color: #94a3b8;
  font-size: 10px;
}
.diff-body {
  max-height: none;
}
.diff-scroll {
  max-height: 420px;
  overflow-y: auto;
}
.diff-line {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr) minmax(0, 1fr);
  border-top: 1px solid #f1f5f9;
}
.diff-line:first-child {
  border-top: 0;
}
.diff-line .gutter {
  display: grid;
  place-items: center;
  background: #fafafa;
  color: #94a3b8;
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 10px;
}
.diff-cell {
  min-height: 32px;
  padding: 7px 10px;
  font-size: 12px;
  line-height: 1.65;
  color: #1e293b;
  overflow-wrap: anywhere;
}
.diff-cell + .diff-cell {
  border-left: 1px solid #f1f5f9;
}
.diff-line.change {
  background: #fffafa;
}
.diff-line.change .gutter {
  background: #fff1f2;
  color: #be123c;
  font-weight: 700;
}
.diff-line.extra,
.diff-line.missing {
  background: #fffdf5;
}
.diff-line.extra .gutter,
.diff-line.missing .gutter {
  background: #fffbeb;
  color: #b45309;
  font-weight: 700;
}
.seg-expect {
  border-radius: 3px;
  background: #eef2ff;
  color: #4338ca;
  padding: 0 2px;
  font-weight: 600;
}
.seg-bad {
  border-radius: 3px;
  background: #ffe4e6;
  color: #be123c;
  padding: 0 2px;
  font-weight: 600;
}
.seg-extra {
  border-radius: 3px;
  background: #fef3c7;
  color: #92400e;
  padding: 0 2px;
  font-weight: 600;
}
.muted {
  color: #94a3b8;
}
.diff-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  border-top: 1px solid #e8e8ed;
  background: #f8fafc;
  padding: 6px 10px;
  font-size: 10.5px;
  color: #64748b;
}
.legend {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.legend i {
  width: 10px;
  height: 10px;
  border-radius: 3px;
  display: inline-block;
}
.legend-same {
  background: #dcfce7;
}
.legend-change {
  background: #ffe4e6;
}
.legend-extra {
  background: #fef3c7;
}
.spacer {
  flex: 1;
}

/* 静态多轮 */
.turn-rail {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  border: 1px solid #e8e8ed;
  background: #fff;
  border-radius: 12px;
  padding: 7px 9px;
}
.turn-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  border-radius: 8px;
  padding: 4px 8px;
  font-size: 11px;
  color: #475569;
  transition: 0.15s;
}
.turn-pill:hover {
  border-color: #cbd5e1;
}
.turn-pill.active {
  border-color: #c7d2fe;
  background: #eef2ff;
  color: #4338ca;
  font-weight: 600;
}
.turn-pill .st {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  flex: 0 0 6px;
}
.st.pass {
  background: #10b981;
}
.st.fail {
  background: #f43f5e;
}
.st.unknown {
  background: #cbd5e1;
}
.mini-count {
  font-size: 9.5px;
  color: #94a3b8;
}
.rail-hint {
  margin-left: auto;
  font-size: 10px;
  color: #64748b;
}
.collapsed-turn {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  border: 1px dashed #e2e8f0;
  background: #fbfcfe;
  border-radius: 10px;
  padding: 6px 9px;
  font-size: 11px;
  color: #64748b;
  text-align: left;
}
.collapsed-turn:hover {
  border-color: #c7d2fe;
  background: #f8f9ff;
}
.collapsed-q {
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.collapsed-sep {
  color: #cbd5e1;
}
.collapsed-exp {
  color: #334155;
  font-weight: 500;
}
.expand-link {
  margin-left: auto;
  color: #4f46e5;
  font-size: 10.5px;
  white-space: nowrap;
}
.turn-id {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 11px;
  font-weight: 700;
  color: #4f46e5;
}
.collapse-btn {
  font-size: 10.5px;
  color: #94a3b8;
}
.collapse-btn:hover {
  color: #475569;
}
.session-expect {
  border: 1px solid #e0e7ff;
  border-radius: 12px;
  background: linear-gradient(180deg, #f8f9ff, #fff);
  padding: 9px 11px;
}
.session-tag {
  display: inline-flex;
  align-items: center;
  border-radius: 6px;
  background: #0f766e;
  color: #fff;
  padding: 1px 6px;
  font-size: 10px;
  font-weight: 600;
}
.session-text {
  margin-top: 5px;
  font-size: 12px;
  line-height: 1.7;
  color: #334155;
}
.fact-mini {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  border-radius: 999px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  padding: 1px 7px;
  font-size: 10px;
  color: #475569;
}
.fact-mini.ok {
  border-color: #a7f3d0;
  background: #ecfdf5;
  color: #047857;
}
.fact-mini.no {
  border-color: #fecdd3;
  background: #fff1f2;
  color: #be123c;
}

/* 动态多轮 */
.dynamic-grid {
  display: grid;
  grid-template-columns: minmax(0, 0.92fr) minmax(0, 1.08fr);
  gap: 10px;
  align-items: start;
}
@media (max-width: 1100px) {
  .dynamic-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
.plan-card,
.timeline-card {
  overflow: hidden;
  border: 1px solid #e8e8ed;
  border-radius: 14px;
  background: #fff;
}
.panel-strip {
  display: flex;
  align-items: center;
  gap: 6px;
  border-bottom: 1px solid #e8e8ed;
  background: #f8fafc;
  padding: 7px 10px;
  font-size: 11px;
  font-weight: 600;
  color: #475569;
}
.panel-sub {
  margin-left: auto;
  font-size: 9.5px;
  font-weight: 400;
  color: #94a3b8;
}
.plan-body {
  padding: 10px 11px 11px;
}
.plan-block + .plan-block {
  margin-top: 10px;
  border-top: 1px solid #f0f0f4;
  padding-top: 9px;
}
.panel-label {
  font-size: 9.5px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: #94a3b8;
  text-transform: uppercase;
}
.goal-text {
  margin-top: 4px;
  font-size: 11.5px;
  line-height: 1.7;
  color: #334155;
}
.goal-meta {
  margin-top: 3px;
  font-size: 10px;
  line-height: 1.55;
  color: #94a3b8;
}
.plan-quote {
  margin-top: 4px;
  border-left: 2px solid #c7d2fe;
  padding-left: 8px;
  font-size: 11px;
  line-height: 1.65;
  color: #475569;
}
.hidden-table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 4px;
}
.hidden-table th {
  text-align: left;
  padding: 0 0 3px;
  font-size: 9px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: #94a3b8;
  font-weight: 600;
}
.hidden-table td {
  border-top: 1px solid #f1f5f9;
  padding: 4px 0;
  font-size: 10.5px;
  color: #334155;
  vertical-align: top;
}
.release {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 1px 6px;
  font-size: 9.5px;
  font-weight: 600;
  white-space: nowrap;
}
.release.ok {
  border: 1px solid #a7f3d0;
  background: #ecfdf5;
  color: #047857;
}
.release.no {
  border: 1px solid #fecdd3;
  background: #fff1f2;
  color: #be123c;
}
.plan-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px;
  margin-top: 5px;
}
.chip-mini {
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  border-radius: 999px;
  padding: 1px 7px;
  font-size: 10px;
  color: #475569;
}
.timeline {
  padding: 10px 11px 2px;
}
.tl-item {
  position: relative;
  padding-left: 18px;
  padding-bottom: 10px;
}
.tl-item::before {
  content: '';
  position: absolute;
  left: 4px;
  top: 8px;
  bottom: -1px;
  width: 1px;
  background: #e8e8ed;
}
.tl-item:last-child::before {
  display: none;
}
.tl-dot {
  position: absolute;
  left: 0;
  top: 3px;
  width: 9px;
  height: 9px;
  border-radius: 999px;
  border: 2px solid #cbd5e1;
  background: #fff;
}
.tl-item.ok .tl-dot {
  border-color: #10b981;
  background: #ecfdf5;
}
.tl-item.fail .tl-dot {
  border-color: #f43f5e;
  background: #ffe4e6;
}
.tl-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
}
.tl-id {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 10.5px;
  font-weight: 700;
  color: #4f46e5;
}
.tl-event {
  display: inline-flex;
  align-items: center;
  border-radius: 5px;
  padding: 1px 5px;
  font-size: 9px;
  font-weight: 600;
}
.tl-event.ok {
  border: 1px solid #a7f3d0;
  background: #ecfdf5;
  color: #047857;
}
.tl-event.no {
  border: 1px solid #fecdd3;
  background: #fff1f2;
  color: #be123c;
}
.tl-event.warn {
  border: 1px solid #fde68a;
  background: #fffbeb;
  color: #92400e;
}
.tl-event.gray {
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  color: #64748b;
}
.tl-q,
.tl-a {
  margin-top: 3px;
  border-radius: 7px;
  padding: 5px 7px;
  font-size: 10.5px;
  line-height: 1.6;
  color: #334155;
}
.tl-q {
  border: 1px solid #f1f5f9;
  background: #f8fafc;
}
.tl-a {
  border: 1px solid #e8e8ed;
  background: #fff;
}
.tl-a-bad {
  border-color: #fecdd3;
  background: #fff7f8;
}
.tl-label {
  margin-right: 6px;
  font-size: 8.5px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: #94a3b8;
}
.tl-hint {
  margin-top: 3px;
  font-size: 9.5px;
  color: #be123c;
}
</style>

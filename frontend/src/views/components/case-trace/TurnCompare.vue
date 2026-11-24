<script setup lang="ts">
import { computed } from 'vue'
import { VERDICT_META, TAG_TONE, fmtScore, type TurnVM } from './model'
import TraceIcon from './TraceIcon.vue'

const props = defineProps<{
  turns: TurnVM[]
  /** 判分模式：只用于把「轮次分」写成对应模式的分母，不影响对照逻辑 */
  gradingMode?: string
  /** 追问自检：存在即渲染「派生轮次 S1 · 不进入对照」说明条（原型同款） */
  selfCheck?: { triggerIndex: number | null } | null
}>()

const emit = defineEmits<{
  (e: 'open', index: number, section: 'turn' | 'judge'): void
  (e: 'page'): void
}>()

interface ValueGap {
  label: string
  expected: number
  actual: number
  diff: number
  pct: number | null
}

/** 从「271.2」「271.2 万元」「1,284.6」里取出可比较的数值；非纯数值返回 null。 */
function parseValue(text: string): number | null {
  const cleaned = text.replace(/[,，\s]/g, '').replace(/[^\d.+-]/g, '')
  if (!cleaned || !/[0-9]/.test(cleaned)) return null
  const value = Number(cleaned)
  return Number.isFinite(value) ? value : null
}

/** 本轮有明确「期望值 vs 实际值」数值差时，才认为存在差异；否则不臆造差异。 */
function valueGaps(turn: TurnVM): ValueGap[] {
  const rows: Array<{ label: string; expected: string; actual: string }> = [
    ...turn.facts.map((fact) => ({ label: fact.label, expected: fact.expected, actual: fact.actual })),
    ...turn.rules.map((rule) => ({ label: rule.name, expected: rule.expected, actual: rule.actual })),
  ]
  // 事实与规则常对同一个数各记一条，按「期望 / 实际」合并，避免同一组数字重复两遍
  const byPair = new Map<string, ValueGap>()
  for (const row of rows) {
    const expected = parseValue(row.expected)
    const actual = parseValue(row.actual)
    if (expected === null || actual === null || expected === actual) continue
    const key = `${expected}|${actual}`
    if (byPair.has(key)) continue
    const diff = actual - expected
    byPair.set(key, {
      label: row.label,
      expected,
      actual,
      diff,
      pct: expected === 0 ? null : (diff / expected) * 100,
    })
  }
  return [...byPair.values()]
}

function fmtNum(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/0$/, '')
}

function diffState(turn: TurnVM): 'ok' | 'warn' | 'none' {
  if (!turn.expected) return 'none'
  if (turn.verdict === 'unknown') return 'none'
  // 只有可核对的数值差不臆造：有数值差或判定失败才算「存在差异」；
  // 部分命中但数值一致（只是字段缺失）按「与期望一致」呈现，规则缺失在下方列出。
  if (valueGaps(turn).length) return 'warn'
  if (turn.verdict === 'fail') return 'warn'
  return 'ok'
}

const multiTurn = computed(() => props.turns.length > 1)

function failedRules(turn: TurnVM): string {
  return turn.rules
    .filter((rule) => !rule.pass)
    .map((rule) => (rule.why ? `${rule.name}（${rule.why}）` : rule.name))
    .join('、')
}

function diffHeadline(turn: TurnVM): string {
  if (!turn.expected) return multiTurn.value ? '本轮无文本期望' : '无文本期望答案'
  if (turn.verdict === 'unknown') return '未判定'
  const count = valueGaps(turn).length
  if (count) return `${count} 处数值差异`
  if (turn.verdict === 'pass') return '与期望一致'
  return '数值与期望一致 · 判定未通过'
}

/** 多轮不拿会话级期望硬套某一轮；空期望是可解释的数据缺失，不是前端缺陷。 */
function reasonOf(turn: TurnVM): string {
  if (turn.judgeReason) return turn.judgeReason
  if (multiTurn.value && turn.sessionJudgeReason) return `（会话级，不归属本轮）${turn.sessionJudgeReason}`
  return ''
}

function diffNote(turn: TurnVM): string {
  if (!turn.expected) {
    // 真实库的快照大多没有 turns[].expected_answer，期望常以 expected_rules 表达：
    // 不能一句「补上期望答案」把规则型期望说成没写期望。
    const tail = turn.rules.length ? '；期望若以 expected_rules 表达，请以该轮的规则命中为准。' : '。'
    return multiTurn.value
      ? `多轮用例没有为第 ${turn.index} 轮维护文本期望（用例快照 turns[].expected_answer），因此无法逐轮对照${tail}`
      : `用例快照未提供文本期望答案（expected_answer），无法做数值对照${tail}`
  }
  if (turn.verdict === 'unknown') {
    return props.gradingMode === 'ANSWER_CONTRACT'
      ? '本轮没有落库契约闸门事实，后端也未提供轮次级质量分，无法判定口径是否一致。'
      : '本轮没有规则 / 契约门槛，后端也未提供轮次分，无法判定口径是否一致。'
  }
  const gaps = valueGaps(turn)
  if (gaps.length) {
    const head = gaps[0]
    return reasonOf(turn) || `${head.label}实际 ${fmtNum(head.actual)}，期望 ${fmtNum(head.expected)}。`
  }
  if (turn.verdict === 'pass') return ''
  return reasonOf(turn) || failedRules(turn)
}

const summary = computed(() => {
  const total = props.turns.length
  const same = props.turns.filter((turn) => diffState(turn) === 'ok').length
  const diff = props.turns.filter((turn) => diffState(turn) === 'warn').length
  const judgeFail = props.turns.filter((turn) => turn.verdict === 'fail' || turn.verdict === 'warn').length
  const unknown = props.turns.filter((turn) => turn.verdict === 'unknown').length
  const noExpect = props.turns.filter((turn) => !turn.expected).length
  return { total, same, diff, judgeFail, unknown, noExpect }
})
</script>

<template>
  <div class="cmp-list">
    <div class="cssum">
      <span class="s ok"><TraceIcon name="check" :size="10" /> 与期望一致 <b>{{ summary.same }}</b> 轮</span>
      <span v-if="summary.diff" class="s warn"><TraceIcon name="alert" :size="10" /> 存在差异 <b>{{ summary.diff }}</b> 轮</span>
      <span v-if="summary.judgeFail" class="s bad">
        <TraceIcon name="x" :size="10" /> 判定未通过 <b>{{ summary.judgeFail }}</b> 轮
      </span>
      <span v-if="summary.noExpect" class="s">
        <TraceIcon name="info" :size="10" /> 无文本期望 <b>{{ summary.noExpect }}</b> 轮
      </span>
      <span v-if="summary.unknown" class="s">
        <TraceIcon name="info" :size="10" /> 未判定 <b>{{ summary.unknown }}</b> 轮
      </span>
      <span class="hint">
        <TraceIcon name="info" :size="10" />
        左列 Agent 实际回复，右列用例快照中的期望回复；点「详情」看该轮完整档案
      </span>
    </div>

    <article
      v-for="turn in turns"
      :key="turn.index"
      class="cmpcard"
      :class="`st-${turn.verdict}`"
    >
      <header>
        <span class="tnum">T{{ turn.index }}</span>
        <span class="stat-pill" :class="VERDICT_META[turn.verdict].tone">
          <TraceIcon
            :name="turn.verdict === 'pass' ? 'check' : turn.verdict === 'warn' ? 'alert' : turn.verdict === 'unknown' ? 'info' : 'x'"
            :size="10"
          />
          {{ VERDICT_META[turn.verdict].label }}
        </span>
        <span class="cq" :title="turn.query">{{ turn.query || '（无提问）' }}</span>
        <button
          type="button"
          class="cscore"
          :class="turn.verdict"
          :title="`查看 T${turn.index} 的判定明细（规则 / 契约 / 评分）`"
          @click="emit('open', turn.index, 'judge')"
        >
          <template v-if="turn.score !== null">{{ fmtScore(turn.score) }}<small> / 100</small></template>
          <template v-else>判定 <TraceIcon name="chevron-r" :size="10" /></template>
        </button>
        <button
          type="button"
          class="cdetail"
          :title="`查看 T${turn.index} 的完整档案（本轮 · 判定 · 执行 · 证据）`"
          @click="emit('open', turn.index, 'turn')"
        >
          <TraceIcon name="layers" :size="10" /> 详情
        </button>
      </header>

      <div class="cmpgrid">
        <div class="cmpcol">
          <div class="ch"><TraceIcon name="bot" :size="10" /> Agent 实际回复</div>
          <div class="cb">{{ turn.answer || '（本轮没有回复内容）' }}</div>
        </div>
        <div class="cmpcol expect">
          <div class="ch"><TraceIcon name="target" :size="10" /> 期望回复 · {{ turn.expectedSource || '用例快照' }}</div>
          <div class="cb" :class="{ missing: !turn.expected }">
            {{ turn.expected || (turn.rules.length ? '本轮没有维护文本期望答案（expected_answer）；判定依据见下方规则命中。' : '本轮没有维护文本期望答案（expected_answer）。') }}
          </div>
        </div>
      </div>

      <div class="cmpfoot">
        <div class="ft" :class="diffState(turn) === 'ok' ? 'ok' : diffState(turn) === 'warn' ? 'warn' : ''">
          <TraceIcon :name="diffState(turn) === 'ok' ? 'check' : diffState(turn) === 'warn' ? 'alert' : 'info'" :size="10" />
          <span>{{ diffHeadline(turn) }}</span>
        </div>
        <div v-if="diffNote(turn)" class="note">{{ diffNote(turn) }}</div>
        <div v-if="valueGaps(turn).length" class="cmpdiff">
          <div v-for="gap in valueGaps(turn)" :key="gap.label">
            <div class="gap-label">{{ gap.label }}</div>
            <div class="diff">
              <div class="dcell">
                <div class="dl">期望</div>
                <div class="dv">{{ fmtNum(gap.expected) }}</div>
              </div>
              <div class="dcell">
                <div class="dl">实际</div>
                <div class="dv">{{ fmtNum(gap.actual) }}</div>
              </div>
              <div class="dcell warn">
                <div class="dl">差异</div>
                <div class="dv">{{ gap.diff > 0 ? '+' : '' }}{{ fmtNum(gap.diff) }}</div>
                <div v-if="gap.pct !== null" class="dn-note">{{ gap.pct > 0 ? '+' : '' }}{{ gap.pct.toFixed(2) }}%</div>
              </div>
            </div>
          </div>
        </div>
        <div v-if="turn.rules.length" class="rulemini">
          <span v-for="(rule, index) in turn.rules" :key="index" class="rm" :class="rule.pass ? 'ok' : 'bad'">
            <TraceIcon :name="rule.pass ? 'check' : 'alert'" :size="9" />
            {{ rule.name }}
          </span>
        </div>
        <div v-if="turn.tags.length" class="tagmini">
          <span v-for="tag in turn.tags" :key="tag" class="etag" :class="TAG_TONE[tag] ?? 'slate'">
            <i /> {{ tag }}
          </span>
        </div>
      </div>
    </article>

    <div v-if="selfCheck" class="cmp-self">
      <span class="sw-badge"><TraceIcon name="refresh" :size="10" />派生轮次 S1</span>
      <span>
        {{
          selfCheck.triggerIndex
            ? `追问自检由 T${selfCheck.triggerIndex} 的失败判定触发，只有实际回复、没有期望答案，因此不进入对照。`
            : '追问自检记录未保存触发轮次，只有实际回复、没有期望答案，因此不进入对照。'
        }}
      </span>
      <span class="sp" />
      <button type="button" @click="emit('page')">查看完整自检 <TraceIcon name="chevron-r" :size="9" /></button>
    </div>
  </div>
</template>

<style scoped>
.cmp-list { padding: 14px 16px 6px; }
.cssum { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; padding: 0 2px 12px; }
.cssum .s {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 1px solid #e8e8ed;
  background: #fff;
  border-radius: 999px;
  padding: 3px 9px;
  font-size: 10px;
  color: #6b7280;
}
.cssum .s b { font-family: Consolas, monospace; font-size: 9.5px; font-weight: 600; color: #374151; }
.cssum .s.ok { background: #ecfdf5; border-color: #a7f3d0; color: #047857; }
.cssum .s.ok b { color: #047857; }
.cssum .s.warn { background: #fffbeb; border-color: #fde68a; color: #b45309; }
.cssum .s.warn b { color: #b45309; }
.cssum .s.bad { background: #fff1f2; border-color: #fecdd3; color: #be123c; }
.cssum .s.bad b { color: #be123c; }
.cssum .hint { display: inline-flex; align-items: center; gap: 5px; font-size: 9.5px; color: #9ca3af; margin-left: auto; text-align: right; }
.cmpcard {
  border: 1px solid #e8e8ed;
  border-radius: 14px;
  background: #fff;
  margin-bottom: 11px;
  overflow: hidden;
  box-shadow: 0 1px 2px rgba(17, 24, 39, 0.03);
}
.cmpcard.st-fail { border-color: #fecdd3; }
.cmpcard.st-warn { border-color: #fde68a; }
.cmpcard.st-unknown { border-color: #e2e8f0; border-style: dashed; }
.cmpcard > header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 13px;
  border-bottom: 1px solid #f0f0f4;
  background: #fcfcfe;
  flex-wrap: wrap;
}
.tnum {
  font-family: Consolas, monospace;
  font-size: 9.5px;
  font-weight: 700;
  color: #4f46e5;
  background: #eef2ff;
  border-radius: 6px;
  padding: 3px 6px;
  flex: none;
}
.stat-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 9.5px;
  border-radius: 999px;
  padding: 1.5px 7px;
  border: 1px solid #e8e8ed;
  background: #fff;
  color: #6b7280;
  white-space: nowrap;
}
.stat-pill.pass { background: #ecfdf5; border-color: #a7f3d0; color: #047857; }
.stat-pill.warn { background: #fffbeb; border-color: #fde68a; color: #b45309; }
.stat-pill.fail { background: #fff1f2; border-color: #fecdd3; color: #be123c; }
.stat-pill.unknown { background: #f8fafc; border-color: #e2e8f0; color: #64748b; }
.cq {
  flex: 1 1 200px;
  min-width: 0;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.55;
  color: #111827;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cscore {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 10px;
  color: #6b7280;
  background: none;
  border: 0;
  cursor: pointer;
  border-radius: 6px;
  padding: 2px 5px;
}
.cscore:hover { background: #eef2ff; color: #4f46e5; }
.cscore small { font-size: 8.5px; color: #9ca3af; }
.cscore.pass { color: #047857; }
.cscore.fail { color: #be123c; }
.cscore.warn { color: #b45309; }
.cscore.unknown { color: #64748b; }
.cdetail {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 9.5px;
  color: #6b7280;
  background: #fff;
  border: 1px solid #e8e8ed;
  border-radius: 6px;
  padding: 2px 7px;
  cursor: pointer;
}
.cdetail:hover { border-color: #e0e7ff; color: #4f46e5; }
.cmpgrid { display: grid; grid-template-columns: 1fr 1fr; }
.cmpcol { padding: 10px 13px; min-width: 0; }
.cmpcol.expect { background: #fbfbff; border-left: 1px dashed #e8e8ed; }
.cmpcol .ch {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: #9ca3af;
  margin-bottom: 6px;
}
.cmpcol.expect .ch { color: #4f46e5; }
.cmpcol .cb {
  font-size: 11.5px;
  line-height: 1.9;
  color: #374151;
  word-break: break-word;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.cmpcol.expect .cb { color: #111827; }
.cmpcol.expect .cb.missing { color: #9ca3af; font-style: italic; }
.cmpfoot { border-top: 1px dashed #e8e8ed; padding: 9px 13px; background: #fcfcfe; }
.cmpfoot .ft { display: flex; align-items: flex-start; gap: 6px; font-size: 9.5px; font-weight: 500; color: #6b7280; line-height: 1.6; }
.cmpfoot .ft.ok { color: #059669; }
.cmpfoot .ft.warn { color: #b45309; }
.cmpfoot .ft :deep(svg) { margin-top: 2px; }
.cmpfoot .note { font-size: 10px; line-height: 1.7; color: #6b7280; margin-top: 3px; }
.cmpdiff { margin-top: 7px; }
.cmpdiff .gap-label { font-size: 9px; letter-spacing: 0.06em; font-weight: 600; color: #9ca3af; margin: 6px 0 5px; }
.cmpdiff .diff { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px; }
.dcell { border: 1px solid #f0f0f4; border-radius: 9px; padding: 7px 10px; background: #fff; min-width: 0; }
.dcell .dl { font-size: 9px; letter-spacing: 0.08em; font-weight: 600; color: #9ca3af; }
.dcell .dv { font-family: Consolas, monospace; font-size: 12.5px; font-weight: 600; color: #111827; margin-top: 3px; }
.dcell .dn-note { font-size: 9px; color: #9ca3af; margin-top: 2px; }
.dcell.warn { border-color: #fde68a; background: #fffdf5; }
.dcell.warn .dv { color: #b45309; }
.rulemini { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 7px; }
.rm { display: inline-flex; align-items: center; gap: 3px; font-size: 9px; border-radius: 999px; padding: 1px 6px; border: 1px solid #e8e8ed; color: #6b7280; background: #fff; }
.rm.ok { border-color: #a7f3d0; background: #ecfdf5; color: #047857; }
.rm.bad { border-color: #fde68a; background: #fffbeb; color: #b45309; }
.tagmini { display: flex; flex-wrap: wrap; gap: 4px 10px; margin-top: 7px; }
.etag { display: inline-flex; align-items: center; gap: 5px; font-size: 9px; color: #374151; }
.etag i { width: 5px; height: 5px; border-radius: 50%; background: #cbd5e1; }
.etag.rose i { background: #e11d48; }
.etag.amber i { background: #f59e0b; }
.etag.sky i { background: #0ea5e9; }
.etag.violet i { background: #8b5cf6; }
.etag.orange i { background: #f97316; }
.cmp-self {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin: 2px 0 10px;
  padding: 9px 12px;
  border: 1px dashed #fcd34d;
  border-radius: 11px;
  background: #fffdf7;
  font-size: 10px;
  line-height: 1.7;
  color: #92400e;
}
.cmp-self .sw-badge {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: #fff;
  border: 1px solid #fcd34d;
  border-radius: 6px;
  padding: 1.5px 7px;
  font-size: 9.5px;
  font-weight: 600;
  color: #92400e;
}
.cmp-self .sp { flex: 1; }
.cmp-self button {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: 9.5px;
  color: #92400e;
  background: none;
  border: 0;
  padding: 0;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
}
@media (max-width: 760px) {
  .cmpgrid { grid-template-columns: 1fr; }
  .cmpcol.expect { border-left: 0; border-top: 1px dashed #e8e8ed; }
  .cssum .hint { margin-left: 0; }
}
</style>

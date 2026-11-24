<script setup lang="ts">
import { computed } from 'vue'
import { VERDICT_META, fmtMs, type TurnVM } from './model'
import TraceIcon from './TraceIcon.vue'

const props = defineProps<{
  turn: TurnVM
  model: string
  /** 页面级「全部折叠」：只收起气泡正文，T 行与两个入口始终可见 */
  collapsed?: boolean
}>()

const emit = defineEmits<{
  (e: 'open', section: 'turn' | 'judge' | 'exec' | 'evid' | 'context'): void
  (e: 'toggle'): void
}>()

const verdict = computed(() => VERDICT_META[props.turn.verdict])

/** 回复正文按行渲染，去掉原型里的 :icon: 标记，避免把内部标记当成正文。 */
const lines = computed(() => {
  const raw = props.turn.answer.replace(/:icon:[a-z-]+:/gi, '')
  if (!raw.trim()) return []
  return raw.split(/\n{2,}/).map((block) => block.trim()).filter(Boolean)
})

/** 本轮真实取数（raw_db_data）——只展示标量，不猜结构。 */
const scalarRows = computed<Array<[string, string]>>(() => {
  const raw = props.turn.turn.raw_db_data
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return []
  return Object.entries(raw as Record<string, unknown>)
    .filter(([, value]) => value === null || typeof value !== 'object')
    .map(([key, value]) => [key, value === null ? '—' : String(value)])
})
const DATA_CLAMP = 8
const dataRows = computed(() => scalarRows.value.slice(0, DATA_CLAMP))
/** 只展示前 8 项时要说明还有多少项，避免把截断读成全部取数 */
const dataRowsHidden = computed(() => Math.max(0, scalarRows.value.length - dataRows.value.length))

const stepCount = computed(() => props.turn.steps.length)
const timeLabel = computed(() => `${props.turn.at ? props.turn.at.slice(11, 19) : '—'}`)
</script>

<template>
  <div class="turn-block">
    <button
      type="button"
      class="bdivider"
      :aria-expanded="!collapsed"
      :title="collapsed ? '展开本轮对话' : '折叠本轮对话'"
      @click="emit('toggle')"
    >
      <span class="tn">T{{ turn.index }}</span>
      <span v-if="turn.intent" class="intent">{{ turn.intent }}</span>
      <span class="sp" />
      <span class="met">
        <TraceIcon name="bolt" :size="10" />
        {{ fmtMs(turn.ttft) }}
        <span class="dot">·</span>
        {{ fmtMs(turn.latency) }}
        <span class="dot">·</span>
        {{ turn.tokens }} tokens
      </span>
      <span class="fold" :class="{ on: collapsed }">
        {{ collapsed ? '展开' : '收起' }}
        <TraceIcon name="chev" :size="10" />
      </span>
    </button>

    <div v-show="!collapsed" class="brow user">
      <div class="bbubble-user">{{ turn.query || '（本轮没有提问记录）' }}</div>
    </div>

    <div v-show="!collapsed" class="brow ai">
      <div class="bava"><TraceIcon name="bot" :size="13" /></div>
      <div class="bwrap rich">
        <div class="bbubble-ai rich">
          <div class="bmeta">
            <span class="aname">Agent</span>
            <span class="amodel">{{ model || '未记录模型' }}</span>
            <span class="stat-pill" :class="verdict.tone">
              <TraceIcon
                :name="turn.verdict === 'pass' ? 'check' : turn.verdict === 'warn' ? 'alert' : turn.verdict === 'unknown' ? 'info' : 'x'"
                :size="10"
              />
              {{ verdict.label }}
            </span>
            <span class="sp" />
            <span class="met">
              {{ timeLabel }}
              <span class="dot">·</span>
              {{ stepCount }} 步
            </span>
          </div>

          <div v-if="lines.length" class="md">
            <p v-for="(line, index) in lines" :key="index">{{ line }}</p>
          </div>
          <div v-else class="md empty-answer">本轮没有回复正文（可能执行未完成或调用失败）。</div>

          <div v-if="dataRows.length" class="databox">
            <div class="dbh"><TraceIcon name="db" :size="11" /> 本轮工具返回（raw_db_data）</div>
            <div class="dchips">
              <span v-for="([key, value], index) in dataRows" :key="index"><b>{{ key }}</b> {{ value }}</span>
              <span v-if="dataRowsHidden" class="more">另有 {{ dataRowsHidden }} 项 · 见「证据」</span>
            </div>
          </div>

          <div class="ans-foot">
            <button
              type="button"
              class="lk lk-judge"
              :class="turn.verdict"
              title="打开右侧面板的判定段"
              @click="emit('open', 'judge')"
            >
              <TraceIcon name="scale" :size="11" />
              判定
              <span v-if="turn.score !== null" class="score">{{ turn.score }}</span>
              <span class="to"><TraceIcon name="chevron-r" :size="10" /></span>
            </button>
            <span class="evsep" />
            <button type="button" class="lk" title="打开右侧面板的本轮段" @click="emit('open', 'turn')">
              <TraceIcon name="file" :size="11" />
              详情
              <span class="to"><TraceIcon name="chevron-r" :size="10" /></span>
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.turn-block { min-width: 0; }
.bdivider {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  font-size: 9.5px;
  color: #9ca3af;
  margin-bottom: 10px;
  flex-wrap: wrap;
  min-width: 0;
  background: none;
  border: 0;
  padding: 0;
  text-align: left;
  cursor: pointer;
}
.bdivider:hover .fold { color: #4f46e5; border-color: #c7d2fe; }
.bdivider .fold {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  border: 1px solid #e8e8ed;
  border-radius: 5px;
  padding: 1px 5px;
  color: #9ca3af;
  background: #fff;
  font-size: 9px;
  white-space: nowrap;
}
.bdivider .fold.on :deep(svg) { transform: rotate(-90deg); }
.bdivider .fold :deep(svg) { transition: transform 0.15s; }
.bdivider::before,
.bdivider::after { content: ""; height: 1px; flex: 1 1 24px; background: linear-gradient(90deg, transparent, #e8e8ed); }
.bdivider::after { background: linear-gradient(90deg, #e8e8ed, transparent); }
.bdivider .tn {
  font-family: Consolas, monospace;
  font-size: 9px;
  font-weight: 700;
  color: #4f46e5;
  background: #eef2ff;
  border-radius: 5px;
  padding: 2px 6px;
}
.bdivider .intent { color: #6b7280; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bdivider .met { display: inline-flex; align-items: center; gap: 3px; font-family: Consolas, monospace; }
.bdivider .dot { color: #d4d4d8; }
.brow.user { display: flex; justify-content: flex-end; }
.bbubble-user {
  max-width: 62%;
  background: linear-gradient(135deg, #6366f1, #4f46e5);
  color: #fff;
  border-radius: 16px 16px 5px 16px;
  padding: 10px 14px;
  font-size: 12.5px;
  line-height: 1.7;
  box-shadow: 0 12px 26px -20px rgba(79, 70, 229, 0.95);
  word-break: break-word;
  overflow-wrap: anywhere;
  min-width: 0;
  white-space: pre-wrap;
}
.brow.ai { display: flex; gap: 9px; align-items: flex-start; margin-top: 9px; }
.bava {
  width: 26px;
  height: 26px;
  flex: none;
  border-radius: 9px;
  background: linear-gradient(135deg, #818cf8, #4f46e5);
  color: #fff;
  display: grid;
  place-items: center;
}
.bwrap { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.bwrap.rich { flex: 1 1 auto; max-width: 92%; }
.bbubble-ai {
  background: #fff;
  border: 1px solid #e8e8ed;
  border-radius: 5px 16px 16px 16px;
  padding: 10px 15px 8px;
  box-shadow: 0 1px 2px rgba(17, 24, 39, 0.03);
  min-width: 0;
}
.bbubble-ai.rich { width: 100%; }
.bmeta {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  font-size: 9.5px;
  color: #9ca3af;
  margin-bottom: 6px;
}
.bmeta .sp { flex: 1; }
.bmeta .aname { font-size: 11px; font-weight: 600; color: #374151; }
.bmeta .amodel {
  font-family: Consolas, monospace;
  font-size: 9px;
  color: #6b7280;
  background: #f4f4f6;
  border-radius: 5px;
  padding: 1px 5px;
}
.bmeta .met { display: inline-flex; align-items: center; gap: 3px; font-family: Consolas, monospace; }
.bmeta .dot { color: #d4d4d8; }
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
.md { font-size: 12.5px; line-height: 1.9; color: #374151; word-break: break-word; overflow-wrap: anywhere; }
.md p + p { margin-top: 7px; }
.md.empty-answer { color: #9ca3af; font-size: 11.5px; }
.databox { margin-top: 10px; border: 1px solid #f0f0f4; background: #fbfbfd; border-radius: 9px; padding: 7px 9px; }
.dbh { display: flex; align-items: center; gap: 4px; font-size: 9px; color: #9ca3af; margin-bottom: 5px; }
.dchips { display: flex; flex-wrap: wrap; gap: 4px; }
.dchips span {
  font-family: Consolas, monospace;
  font-size: 9px;
  background: #fff;
  border: 1px solid #e8e8ed;
  border-radius: 5px;
  padding: 2px 6px;
  color: #6b7280;
}
.dchips span b { font-weight: 500; color: #374151; }
.dchips span.more { color: #9ca3af; border-style: dashed; background: #fcfcfd; }
.ans-foot {
  display: flex;
  align-items: center;
  gap: 9px;
  flex-wrap: wrap;
  padding: 8px 0 0;
  border-top: 1px dashed #e8e8ed;
  margin-top: 9px;
  font-size: 9.5px;
  color: #9ca3af;
}
.lk {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  line-height: 1;
  color: #6b7280;
  background: none;
  border: 0;
  padding: 1px 0;
  cursor: pointer;
}
.lk:hover { color: #4f46e5; }
.lk .score { font-family: Consolas, monospace; font-size: 9px; color: #374151; background: #f1f5f9; border-radius: 999px; padding: 0 5px; }
/* 判定入口按本轮结论着色：通过=绿 / 部分命中=琥珀 / 未通过=玫红 / 未判定=中性 */
.lk-judge { font-weight: 500; }
.lk-judge.pass { color: #047857; }
.lk-judge.pass:hover { color: #065f46; }
.lk-judge.pass .score { color: #047857; background: #ecfdf5; box-shadow: inset 0 0 0 1px #a7f3d0; }
.lk-judge.warn { color: #b45309; }
.lk-judge.warn:hover { color: #92400e; }
.lk-judge.warn .score { color: #b45309; background: #fffbeb; box-shadow: inset 0 0 0 1px #fde68a; }
.lk-judge.fail { color: #be123c; }
.lk-judge.fail:hover { color: #9f1239; }
.lk-judge.fail .score { color: #be123c; background: #fff1f2; box-shadow: inset 0 0 0 1px #fecdd3; }
.lk-judge.unknown .score { color: #64748b; background: #f8fafc; box-shadow: inset 0 0 0 1px #e2e8f0; }
.lk .to { color: #c3c9d4; margin-left: 1px; }
.evsep { width: 1px; height: 12px; background: #dcdfe6; opacity: 0.7; }
@media (max-width: 980px) {
  .bbubble-user { max-width: 80%; }
  .bwrap.rich { max-width: 100%; }
}
</style>

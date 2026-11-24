<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import type { SelfCheckResult } from '../../../types'
import { copyText } from '../../../utils/clipboard'
import { useToastStore } from '../../../stores/toast'
import { fmtMs, type TurnVM } from './model'
import { statusText } from './format'
import TraceIcon from './TraceIcon.vue'

/**
 * 「追问自检」整页页签 —— 1:1 还原原型 renderSelfCheck：
 * selfDivider 分隔条 + 触发原因 / 自检配置两张卡 + 自检 Prompt 长卡（可展开）+ 自检回答气泡。
 * 数据全部来自 ExecutionRecord.self_check 真实字段，缺失项诚实降级不编造。
 */
const props = defineProps<{
  record: SelfCheckResult
  /** 触发轮（= self_check.turn_index 对应轮），老记录缺失时为 null */
  trigger: TurnVM | null
  turnCount: number
}>()

const emit = defineEmits<{
  (e: 'locate', index: number): void
}>()

const toast = useToastStore()
const promptOpen = ref(false)

/**
 * Prompt 预览折叠：与判分页同一原则 —— 只有真实溢出才截断/虚化并显示「展开全文」。
 * scrollHeight 不受 max-height 影响，开合两态都能量到完整内容高度。
 */
const PROMPT_CLAMP = 104
const promptBody = ref<HTMLElement | null>(null)
const promptOverflow = ref(false)
let promptObserver: ResizeObserver | null = null

function measurePrompt(): void {
  const el = promptBody.value
  if (!el) return
  promptOverflow.value = el.scrollHeight > PROMPT_CLAMP + 4
}

onMounted(() => {
  void nextTick(measurePrompt)
  if (typeof ResizeObserver === 'undefined' || !promptBody.value) return
  promptObserver = new ResizeObserver(() => measurePrompt())
  promptObserver.observe(promptBody.value)
})

onBeforeUnmount(() => {
  promptObserver?.disconnect()
  promptObserver = null
})

const isDone = computed(() => props.record.status === 'DONE')
const triggerIndex = computed(() => props.trigger?.index ?? null)
const triggerLabel = computed(() => (triggerIndex.value ? `T${triggerIndex.value}` : null))
const prompt = computed(() => props.record.prompt || '')
const promptLines = computed(() => (prompt.value ? prompt.value.split('\n').filter(Boolean).length : 0))

/** 模板标识：template_name 优先，退化 template_code（self_check.py:41-79 落库键） */
const template = computed(() => {
  const name = props.record.template_name || props.record.template_code || '失败追问自检'
  const version = props.record.template_version ? ` v${props.record.template_version}` : ''
  return `${name}${version}`
})

/** 触发原因：trigger 是后端固定文案（self_check.py:107「LLM 裁决不通过」），轮次信息由 turn_index 补充 */
const triggerText = computed(() => {
  const base = props.record.trigger || 'LLM 裁决不通过'
  if (!triggerLabel.value) return `${base} —— 评测器在同一会话向 Agent 追加一轮自检提问，要求其自查口径。`
  return `${base} —— ${triggerLabel.value} 轮次判定未通过后，评测器在同一会话向 Agent 追加一轮追问，要求其自查口径。`
})

/** 自检回答正文：SSE 聚合内容（与轮次 response_raw.content 同链，target_api.py:384-385） */
const answerText = computed(() => {
  const raw = props.record.response_raw as Record<string, unknown> | null | undefined
  const content = raw?.content
  return typeof content === 'string' ? content : ''
})
const answerParagraphs = computed(() =>
  answerText.value
    .split(/\n+/)
    .map((part) => part.trim())
    .filter(Boolean),
)

/** 自检回答 tokens：末帧 usage 经深合并落在 metadata.usage（sse.py:63-66），与轮次同回落链 */
const completionTokens = computed(() => {
  const raw = props.record.response_raw as Record<string, unknown> | null | undefined
  const meta = raw?.metadata as Record<string, unknown> | undefined
  const usage = meta?.usage as Record<string, unknown> | undefined
  const value = usage?.completion_tokens
  return typeof value === 'number' ? value : null
})

const conclusionText = computed(() => {
  if (isDone.value) return props.record.conclusion || '（Agent 未给出自检结论）'
  return props.record.error || props.record.skip_reason || '自检未产生结论'
})

/** 【】段落标题加粗 —— 原型 promptBodyHTML 的渲染规则，先转义再标记 */
const promptHtml = computed(() => {
  const escaped = prompt.value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  return escaped.replace(/【([^】]+)】/g, '<b>【$1】</b>')
})

function locateTrigger(): void {
  if (triggerIndex.value) emit('locate', triggerIndex.value)
}

async function copyPrompt(): Promise<void> {
  if (!prompt.value) return
  const ok = await copyText(prompt.value)
  if (ok) toast.success('自检 Prompt 已复制')
  else toast.error('复制失败')
}

async function copyConclusion(): Promise<void> {
  const ok = await copyText(conclusionText.value)
  if (ok) toast.success('自检结论已复制')
  else toast.error('复制失败')
}
</script>

<template>
  <div class="judge">
    <!-- ===== 分隔条（原型 selfDivider，页内形态不带跳转链接） ===== -->
    <div class="selfdiv">
      <span class="tag"><TraceIcon name="refresh" :size="10" /> 追问自检</span>
      <span>
        由 {{ triggerLabel ?? 'LLM 裁决失败' }} 自动触发 · 同一会话追加 1 轮 · 不参与通过判定
      </span>
      <span class="sp" />
    </div>

    <!-- ===== 触发原因 / 自检配置（先交代背景，再给返回） ===== -->
    <div class="snapgrid">
      <div class="sbox">
        <h6><TraceIcon name="alert" :size="10" /> 触发原因</h6>
        <p class="expect">{{ triggerText }}</p>
      </div>
      <div class="sbox">
        <h6><TraceIcon name="file" :size="10" /> 自检配置</h6>
        <div class="varlist">
          <span>template={{ record.template_name || record.template_code || '失败追问自检' }}</span>
          <span v-if="record.template_version">v{{ record.template_version }}</span>
          <span v-if="triggerLabel">触发 {{ triggerLabel }}</span>
          <span>追加 1 轮</span>
          <span>不影响判定</span>
        </div>
        <p class="expect small">
          自检结论只做诊断留存，不改变用例通过与否；实际发出的报文可在「原始报文 · 调用链」中查看。
        </p>
      </div>
    </div>

    <!-- ===== 自检回答（原型 selfAnswerBubble） ===== -->
    <div class="stage-bub">
      <div class="selfans-label">
        <TraceIcon name="bot" :size="11" />
        <span>自检回答</span>
        <span class="m">
          {{ fmtMs(record.latency_ms) }}<template v-if="completionTokens !== null"> · {{ completionTokens }} tokens</template>
        </span>
        <span class="sp" />
        <span class="m">按下文 Prompt 生成</span>
      </div>

      <div class="brow ai">
        <div class="bava amber"><TraceIcon name="bot" :size="13" /></div>
        <div class="bbubble-ai rich amber">
          <div class="bmeta">
            Agent 自检
            <span class="amodel">{{ template }}</span>
            <span class="stat-pill warn"><TraceIcon name="refresh" :size="10" /> 自检轮次</span>
            <span class="stat-pill" :class="isDone ? 'warn' : 'fail'">
              <TraceIcon name="refresh" :size="10" /> {{ statusText(record.status) }}
            </span>
          </div>
          <div v-if="answerParagraphs.length" class="md">
            <p v-for="(paragraph, index) in answerParagraphs" :key="index">{{ paragraph }}</p>
          </div>
          <div class="callout warn">
            <div>
              <b>{{ isDone ? '自检结论' : '说明' }}</b>
              <p>{{ conclusionText }}</p>
            </div>
          </div>
          <div class="ev">
            <button v-if="triggerIndex" type="button" class="ev-chip" title="回到触发轮次" @click="locateTrigger">
              <TraceIcon name="target" :size="10" /> 定位到 {{ triggerLabel }}
            </button>
            <button type="button" class="ev-chip" title="复制自检结论" @click="copyConclusion">
              <TraceIcon name="copy" :size="10" /> 复制结论
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- ===== 自检 Prompt（原型 promptCard，置底作为来源凭据） ===== -->
    <div v-if="prompt" class="promptcard">
      <div class="pc-head">
        <span class="pc-tag"><TraceIcon name="refresh" :size="10" /> 自检 Prompt</span>
        <span class="pc-meta">{{ template }} · 变量已渲染</span>
        <span class="sp" />
        <span class="pc-meta">{{ prompt.length }} 字</span>
      </div>
      <!-- eslint-disable-next-line vue/no-v-html -- 内容已转义，仅【】标题加粗（原型同规则） -->
      <div ref="promptBody" class="pc-body" :class="{ open: promptOpen, clamp: promptOverflow && !promptOpen }" v-html="promptHtml" />
      <div class="pc-foot">
        <span class="cnt">{{ prompt.length }} 字符 · {{ promptLines }} 行</span>
        <span class="sp" />
        <button type="button" @click="copyPrompt"><TraceIcon name="copy" :size="9" /> 复制</button>
        <button v-if="promptOverflow" type="button" @click="promptOpen = !promptOpen">
          <TraceIcon name="chev" :size="9" />
          {{ promptOpen ? '收起' : '展开全文' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ===== 容器（原型 .judge 页内布局） ===== */
.judge {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.stage-bub {
  display: flex;
  flex-direction: column;
  gap: 0;
}

/* ===== 分隔条（原型 .selfdiv，本页形态无右侧链接） ===== */
.selfdiv {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 6px 2px 0;
  padding: 7px 10px;
  border: 1px solid #fcd34d;
  background: #fffbeb;
  border-radius: 9px;
  font-size: 10px;
  color: #b45309;
}
.selfdiv .tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: #fff;
  border: 1px solid #fcd34d;
  border-radius: 5px;
  padding: 1px 6px;
  font-weight: 600;
  font-size: 9.5px;
  color: #92400e;
  white-space: nowrap;
}
.selfdiv .sp { flex: 1; }

/* ===== 触发原因 / 自检配置卡（原型 .snapgrid / .sbox / .varlist / .expect） ===== */
.snapgrid {
  display: grid;
  grid-template-columns: 1.05fr 1fr;
  gap: 12px;
}
.sbox {
  border: 1px solid #e5e7eb;
  border-radius: 11px;
  padding: 11px 12px;
  background: #fff;
}
.sbox h6 {
  font-size: 9px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: #9ca3af;
  margin-bottom: 8px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 6px;
}
.expect {
  font-size: 11px;
  line-height: 1.8;
  color: #374151;
}
.expect.small {
  font-size: 10px;
  color: #6b7280;
  margin-top: 8px;
}
.varlist { display: flex; flex-wrap: wrap; gap: 5px; }
.varlist span {
  font-family: Consolas, monospace;
  font-size: 9.5px;
  color: #0369a1;
  background: #f0f9ff;
  border: 1px solid #bae6fd;
  border-radius: 5px;
  padding: 1px 6px;
  white-space: nowrap;
}

/* ===== 自检 Prompt 长卡（原型 .promptcard） ===== */
.promptcard {
  border: 1px solid #e5e7eb;
  background: #fff;
  border-radius: 11px;
  overflow: hidden;
}
.pc-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid #eef0f3;
  background: #f8f9fb;
  flex-wrap: wrap;
}
.pc-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 9.5px;
  font-weight: 600;
  color: #4b5563;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 5px;
  padding: 1px 6px;
}
.pc-head .pc-meta { font-size: 9.5px; color: #9ca3af; }
.pc-head .sp { flex: 1; }
.pc-body {
  position: relative;
  padding: 11px 14px;
  font-size: 11px;
  line-height: 1.9;
  color: #374151;
  white-space: pre-wrap;
  word-break: break-word;
}
/* 截断只在真实溢出时生效；未溢出则完整展示，不虚化 */
.pc-body.clamp { max-height: 104px; overflow: hidden; }
.pc-body.open { max-height: none; overflow: auto; }
/* 遮罩只在真实溢出且未展开时出现（.clamp 由 scrollHeight 判定挂载） */
.pc-body.clamp::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 46px;
  background: linear-gradient(rgba(255, 255, 255, 0), #fff);
}
.pc-body :deep(b) { color: #111827; font-weight: 600; }
.pc-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 7px 12px;
  border-top: 1px solid #eef0f3;
  background: #f8f9fb;
  font-size: 9px;
  color: #6b7280;
}
.pc-foot .sp { flex: 1; }
.pc-foot .cnt { font-family: Consolas, monospace; font-size: 9px; color: #9ca3af; }
.pc-foot button {
  font-size: 9.5px;
  color: #6b7280;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  border: 1px solid #e5e7eb;
  background: #fff;
  border-radius: 6px;
  padding: 2px 7px;
  cursor: pointer;
  transition: 0.12s;
}
.pc-foot button:hover { background: #f3f4f6; color: #374151; border-color: #d1d5db; }

/* ===== 自检回答（原型 .selfans-label / .brow / .bbubble-ai 琥珀形态） ===== */
.selfans-label {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 6px;
  font-size: 9.5px;
  color: #b45309;
  font-weight: 600;
}
.selfans-label .sp { flex: 1; }
.selfans-label .m {
  font-family: Consolas, monospace;
  font-weight: 400;
  color: #9ca3af;
  font-size: 9px;
}
.brow.ai { display: flex; gap: 9px; align-items: flex-start; margin-top: 0; }
.bava {
  width: 26px;
  height: 26px;
  flex: none;
  border-radius: 9px;
  background: linear-gradient(135deg, #818cf8, #4f46e5);
  color: #fff;
  display: grid;
  place-items: center;
  box-shadow: 0 6px 14px -10px rgba(79, 70, 229, 0.9);
}
.bava.amber {
  background: linear-gradient(135deg, #fbbf24, #f59e0b);
  box-shadow: 0 6px 14px -10px rgba(245, 158, 11, 0.9);
}
.bbubble-ai {
  flex: 0 1 auto;
  max-width: 82%;
  min-width: 0;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 5px 16px 16px 16px;
  padding: 10px 15px 8px;
  box-shadow: 0 1px 2px rgba(17, 24, 39, 0.03);
}
.bbubble-ai.rich { flex: 1 1 auto; max-width: 92%; }
.bbubble-ai.amber { border-color: #fcd34d; background: #fffef9; }
.bmeta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 9.5px;
  color: #9ca3af;
  margin-bottom: 6px;
  flex-wrap: wrap;
}
.amodel {
  font-family: Consolas, monospace;
  font-size: 9px;
  color: #6b7280;
  background: #f4f4f6;
  border-radius: 5px;
  padding: 1px 5px;
}
.stat-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 9.5px;
  border-radius: 999px;
  padding: 1.5px 7px;
  border: 1px solid #e5e7eb;
  background: #fff;
  color: #6b7280;
  white-space: nowrap;
}
.stat-pill.warn { background: #fffbeb; border-color: #fcd34d; color: #b45309; }
.stat-pill.fail { background: #fff1f2; border-color: #fecdd3; color: #e11d48; }
.md { font-size: 12.5px; line-height: 1.9; color: #374151; }
.md p + p { margin-top: 7px; }
.callout {
  margin-top: 11px;
  border: 1px solid #fcd34d;
  background: #fffbeb;
  border-radius: 10px;
  padding: 9px 12px;
}
.callout b { font-size: 10.5px; color: #92400e; display: block; margin-bottom: 2px; }
/* 自检结论来自后端，含换行与编号列表，按原文换行渲染 */
.callout p {
  font-size: 11px;
  line-height: 1.7;
  color: #6b7280;
  white-space: pre-wrap;
  word-break: break-word;
}

/* ===== 操作 chips（原型 .ev / .ev-chip） ===== */
.ev {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
  margin-top: 9px;
  padding-top: 8px;
  border-top: 1px dashed #f0f1f3;
}
.ev-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid #e5e7eb;
  background: #fff;
  border-radius: 7px;
  padding: 2.5px 7px;
  font-size: 9.5px;
  color: #6b7280;
  transition: 0.12s;
  white-space: nowrap;
  cursor: pointer;
}
.ev-chip:hover {
  border-color: #c7d2fe;
  background: #eef2ff;
  color: #4338ca;
}

@media (max-width: 760px) {
  .snapgrid { grid-template-columns: 1fr; }
}
</style>

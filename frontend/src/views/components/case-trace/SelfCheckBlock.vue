<script setup lang="ts">
import { computed, ref } from 'vue'
import type { SelfCheckResult } from '../../../types'
import { copyText } from '../../../utils/clipboard'
import { useToastStore } from '../../../stores/toast'
import { fmtMs, type TurnVM } from './model'
import { statusText } from './format'
import TraceIcon from './TraceIcon.vue'

const props = defineProps<{
  record: SelfCheckResult
  trigger: TurnVM | null
  model: string
  turnCount: number
  open: boolean
}>()

const emit = defineEmits<{
  (e: 'toggle'): void
  (e: 'locate', index: number): void
  (e: 'page', value: 'selfcheck'): void
  (e: 'judge', index: number): void
}>()

const toast = useToastStore()
const promptOpen = ref(false)

const isDone = computed(() => props.record.status === 'DONE')
const triggerIndex = computed(() => props.trigger?.index ?? null)
const triggerLabel = computed(() => (triggerIndex.value ? `T${triggerIndex.value}` : '本次执行'))
/** 后端 self_check 只记录触发原因，没有触发轮次字段：定位不到时按执行级描述，不推测轮次 */
const triggerPhrase = computed(() => (triggerIndex.value ? `T${triggerIndex.value} 未通过` : '本次执行判失败'))
const verdictScope = computed(() => (triggerIndex.value ? `${triggerLabel.value} 的判定结论` : '本次执行的判定结论'))
const conclusion = computed(() => {
  if (isDone.value) return props.record.conclusion || '（Agent 未给出自检结论）'
  return props.record.skip_reason || props.record.error || '自检未产生结论'
})
const prompt = computed(() => props.record.prompt || '')
const promptChars = computed(() => prompt.value.length)
const template = computed(() => {
  const name = props.record.template_name || '失败追问自检'
  const version = props.record.template_version ? ` v${props.record.template_version}` : ''
  return `${name}${version}`
})

function locate(): void {
  if (triggerIndex.value) emit('locate', triggerIndex.value)
}

async function copyPrompt(): Promise<void> {
  if (!prompt.value) return
  const ok = await copyText(prompt.value)
  if (ok) toast.success('自检 Prompt 已复制')
  else toast.error('复制失败')
}
</script>

<template>
  <div class="selfwrap" :class="{ collapsed: !open }">
    <header class="sw-head" @click="emit('toggle')">
      <span class="sw-badge"><TraceIcon name="spark" :size="10" /> 派生轮次 S1</span>
      <span class="sw-title">追问自检</span>
      <span class="sw-meta">
        <TraceIcon name="refresh" :size="10" />
        {{ triggerPhrase }}后自动触发 · 不计入 {{ turnCount }} 轮 · 不参与通过判定
      </span>
      <span class="sp" />
      <button v-if="triggerIndex" type="button" class="sw-act" title="回到触发轮" @click.stop="locate">
        <TraceIcon name="target" :size="9" /> 定位 {{ triggerLabel }}
      </button>
      <button type="button" class="sw-act" title="追问自检页签" @click.stop="emit('page', 'selfcheck')">
        <TraceIcon name="file" :size="9" /> 完整自检
        <span class="to"><TraceIcon name="arrow" :size="9" /></span>
      </button>
      <button type="button" class="sw-act sw-fold" :aria-expanded="open" @click.stop="emit('toggle')">
        <TraceIcon name="chev" :size="9" />
        {{ open ? '收起' : '展开' }}
      </button>
    </header>

    <div v-if="open" class="sw-body">
      <div class="sw-note">
        <TraceIcon name="shield" :size="11" />
        <span>
          隔离说明：自检是评测器在 {{ triggerPhrase }}后追加的一次诊断调用 —— 提问由系统生成（不是用户消息），
          回答单独归档；它不计入 {{ turnCount }} 轮对话，也不改变 {{ verdictScope }}。
        </span>
      </div>

      <div class="promptcard">
        <div class="pc-head">
          <span class="pc-meta">{{ template }}</span>
          <span class="sp" />
          <span class="pc-origin" title="这段 Prompt 由系统生成，并已原样发送给 Agent">
            <TraceIcon name="bot" :size="10" />系统生成 · 已发送给 Agent
          </span>
        </div>
        <div class="pc-body" :class="{ open: promptOpen }">{{ prompt || '（未记录自检 Prompt）' }}</div>
        <div class="pc-foot">
          <span class="cnt">{{ promptChars }} 字符</span>
          <span class="sp" />
          <button v-if="prompt" type="button" @click="promptOpen = !promptOpen">
            <TraceIcon name="chev" :size="9" />
            {{ promptOpen ? '收起' : '展开全文' }}
          </button>
          <button v-if="prompt" type="button" @click="copyPrompt">
            <TraceIcon name="copy" :size="9" /> 复制 Prompt
          </button>
        </div>
      </div>

      <div class="sw-ansbar">
        <TraceIcon name="bot" :size="11" />
        <span>自检返回</span>
        <span class="m">{{ fmtMs(record.latency_ms) }} · {{ statusText(record.status) }}</span>
        <span class="line" />
        <span class="m">由 S1 自动发起 · 非用户提问</span>
      </div>

      <div class="self-answer">
        <div class="sa-head">
          <span class="bava"><TraceIcon name="bot" :size="12" /></span>
          <b>追问自检结论</b>
          <span class="stat-pill" :class="isDone ? 'warn' : 'fail'">{{ statusText(record.status) }}</span>
          <span class="sp" />
          <span class="met">{{ model || '未记录模型' }}</span>
        </div>
        <p class="sa-body">{{ conclusion }}</p>
        <div class="sa-foot">
          <button v-if="triggerIndex" type="button" class="lk" @click="emit('judge', triggerIndex)">
            <TraceIcon name="scale" :size="11" /> 看 {{ triggerLabel }} 判定
            <span class="to"><TraceIcon name="chevron-r" :size="10" /></span>
          </button>
          <span class="evsep" />
          <button type="button" class="lk" @click="emit('page', 'selfcheck')">
            <TraceIcon name="file" :size="11" /> 完整自检记录
            <span class="to"><TraceIcon name="arrow" :size="10" /></span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.selfwrap {
  margin: 26px 0 6px;
  border: 1px dashed #fcd34d;
  border-radius: 14px;
  background: #fffdf7;
  box-shadow: 0 1px 2px rgba(146, 64, 14, 0.04);
  overflow: hidden;
}
.sw-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  background: linear-gradient(180deg, #fffbeb, #fffdf7);
  border-bottom: 1px dashed #fde68a;
  flex-wrap: wrap;
  cursor: pointer;
}
.sw-badge {
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
  white-space: nowrap;
}
.sw-title { font-size: 12px; font-weight: 600; color: #92400e; white-space: nowrap; }
.sw-meta { display: inline-flex; align-items: center; gap: 4px; font-size: 9.5px; color: #b45309; flex-wrap: wrap; }
.sw-head .sp { flex: 1; }
.sw-act {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 9.5px;
  color: #92400e;
  background: #fff;
  border: 1px solid #fcd34d;
  border-radius: 6px;
  padding: 2px 7px;
  white-space: nowrap;
  cursor: pointer;
}
.sw-act:hover { background: #fef3c7; }
.sw-fold :deep(svg) { transition: transform 0.15s; }
.selfwrap.collapsed .sw-fold :deep(svg) { transform: rotate(-90deg); }
.selfwrap.collapsed .sw-body { display: none; }
.sw-body { padding: 11px 13px 13px; }
.sw-note {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: 10px;
  line-height: 1.75;
  color: #92400e;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 8px;
  padding: 7px 9px;
  margin-bottom: 12px;
}
.sw-note :deep(svg) { margin-top: 2px; }
.promptcard { border: 1px solid #fde68a; background: #fffdf5; border-radius: 11px; overflow: hidden; margin-bottom: 12px; }
.pc-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  border-bottom: 1px dashed #fde68a;
  background: #fffbeb;
  flex-wrap: wrap;
}
.pc-head .pc-meta { font-size: 9.5px; color: #b45309; }
.pc-head .sp { flex: 1; }
/* 这段 Prompt 的系统来源与去向是本卡的语义重点：放右侧、独立成胶囊 */
.pc-origin {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 9.5px;
  font-weight: 600;
  color: #92400e;
  background: #fff;
  border: 1px solid #fbbf24;
  border-radius: 999px;
  padding: 2px 9px;
  white-space: nowrap;
}
.pc-body {
  position: relative;
  padding: 11px 14px;
  font-size: 11px;
  line-height: 1.9;
  color: #374151;
  white-space: pre-wrap;
  max-height: 104px;
  overflow: hidden;
  word-break: break-word;
}
.pc-body.open { max-height: none; }
.pc-body:not(.open)::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 46px;
  background: linear-gradient(rgba(255, 253, 245, 0), #fffdf5);
}
.pc-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 7px 12px;
  border-top: 1px dashed #fde68a;
  background: #fffdf5;
  font-size: 9px;
  color: #6b7280;
}
.pc-foot .sp { flex: 1; }
.pc-foot .cnt { font-family: Consolas, monospace; color: #9ca3af; }
.pc-foot button {
  font-size: 9.5px;
  color: #92400e;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  border: 1px solid #fde68a;
  background: #fff;
  border-radius: 6px;
  padding: 2px 7px;
  cursor: pointer;
}
.pc-foot button:hover { background: #fffbeb; }
.sw-ansbar { display: flex; align-items: center; gap: 6px; margin: 14px 0 8px 2px; font-size: 9.5px; font-weight: 600; color: #b45309; }
.sw-ansbar .line { flex: 1; height: 1px; background: linear-gradient(90deg, #fde68a, rgba(253, 230, 138, 0)); }
.sw-ansbar .m { font-family: Consolas, monospace; font-size: 9px; font-weight: 400; color: #9ca3af; }
.self-answer { border: 1px solid #fcd34d; background: #fffef9; border-radius: 5px 14px 14px 14px; padding: 10px 14px 8px; }
.sa-head { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; font-size: 9.5px; color: #9ca3af; }
.sa-head .sp { flex: 1; }
.sa-head b { font-size: 11px; color: #374151; }
.sa-head .met { font-family: Consolas, monospace; font-size: 9px; }
.bava {
  width: 24px;
  height: 24px;
  flex: none;
  border-radius: 8px;
  background: linear-gradient(135deg, #fbbf24, #f59e0b);
  color: #fff;
  display: grid;
  place-items: center;
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
.stat-pill.warn { background: #fffbeb; border-color: #fde68a; color: #b45309; }
.stat-pill.fail { background: #fff1f2; border-color: #fecdd3; color: #be123c; }
.sa-body { font-size: 11.5px; line-height: 1.85; color: #374151; margin-top: 7px; white-space: pre-wrap; word-break: break-word; }
.sa-foot { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; padding-top: 8px; margin-top: 8px; border-top: 1px dashed #fde68a; }
.lk { display: inline-flex; align-items: center; gap: 4px; font-size: 10px; line-height: 1; color: #6b7280; background: none; border: 0; padding: 1px 0; cursor: pointer; }
.lk:hover { color: #4f46e5; }
.lk .to { color: #c3c9d4; margin-left: 1px; }
.evsep { width: 1px; height: 12px; background: #dcdfe6; opacity: 0.7; }
</style>

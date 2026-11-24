<script setup lang="ts">
import { computed } from 'vue'
import { copyText } from '../../../utils/clipboard'
import { useToastStore } from '../../../stores/toast'
import { byteSize, highlightJson, lineCount, pretty } from './format'
import TraceIcon from './TraceIcon.vue'

const props = withDefaults(
  defineProps<{
    title?: string
    value: unknown
    /** json = 高亮；text = 纯文本；headers = 键值表 */
    kind?: 'json' | 'text' | 'headers'
    note?: string
    empty?: string
    maxHeight?: string
    /** 覆盖复制到剪贴板的内容（默认按 kind 生成） */
    copyAs?: string
  }>(),
  { kind: 'json', maxHeight: '46vh', empty: '未记录' },
)

const toast = useToastStore()

const isHeaders = computed(() => props.kind === 'headers')
const headers = computed<Array<[string, string]>>(() =>
  Array.isArray(props.value) ? (props.value as Array<[string, string]>) : [],
)
// 落库字段缺失时调用方传 null：JSON.stringify(null) 会得到字符串 "null"，
// 页面上就成了有内容的「null」并能被复制。这里统一按未记录处理，交给 empty 文案。
const normalized = computed(() => (props.value === null ? undefined : props.value))
const body = computed(() => pretty(normalized.value))
const hasBody = computed(() => isHeaders.value ? headers.value.length > 0 : Boolean(body.value))
const size = computed(() => (isHeaders.value ? '' : byteSize(normalized.value)))
const lines = computed(() => (isHeaders.value ? 0 : lineCount(normalized.value)))

const rendered = computed(() => {
  if (props.kind === 'text') {
    return body.value
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
  }
  return highlightJson(normalized.value)
})

async function copy(): Promise<void> {
  let text = props.copyAs ?? ''
  if (!text) {
    if (isHeaders.value) text = headers.value.map(([key, value]) => `${key}: ${value}`).join('\n')
    else text = typeof normalized.value === 'string' ? normalized.value : body.value
  }
  if (!text) {
    toast.info('没有可复制的内容')
    return
  }
  const ok = await copyText(text)
  if (ok) toast.success('已复制')
  else toast.error('复制失败')
}
</script>

<template>
  <div class="cbox">
    <div class="mhd">
      <span v-if="title">{{ title }}</span>
      <span v-if="note" class="badge">{{ note }}</span>
      <span class="sp" />
      <span v-if="size" class="sz">{{ size }} · {{ lines }} 行</span>
      <span v-else-if="isHeaders && headers.length" class="sz">{{ headers.length }} 项</span>
      <slot name="actions" />
      <button v-if="hasBody" type="button" class="mcopy" title="复制到剪贴板" @click="copy">
        <TraceIcon name="copy" :size="10" />
        复制
      </button>
    </div>
    <div v-if="!hasBody" class="cempty">{{ empty }}</div>
    <div v-else-if="isHeaders" class="hdrs">
      <div v-for="([key, value], index) in headers" :key="index" class="hdr">
        <span class="k">{{ key }}</span>
        <span class="v">{{ value }}</span>
      </div>
    </div>
    <pre v-else class="code" :style="{ maxHeight }" v-html="rendered" />
  </div>
</template>

<style scoped>
.cbox { min-width: 0; }
.mhd {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 0 6px;
  font-size: 9px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #9ca3af;
  font-weight: 600;
}
.mhd .sp { flex: 1; }
.mhd .badge {
  letter-spacing: 0;
  text-transform: none;
  font-weight: 500;
  font-size: 9px;
  color: #6b7280;
  background: #f4f4f7;
  border-radius: 5px;
  padding: 1px 6px;
}
.mhd .sz { font-family: Consolas, monospace; font-size: 8.5px; color: #b0b6c1; letter-spacing: 0; }
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
.code {
  border: 1px solid #e8e8ed;
  border-radius: 10px;
  background: #fcfcfe;
  padding: 10px 12px;
  font-family: Consolas, "Cascadia Code", monospace;
  font-size: 10.5px;
  line-height: 1.75;
  overflow: auto;
  color: #374151;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  tab-size: 2;
}
.code :deep(.j-key) { color: #7c3aed; }
.code :deep(.j-str) { color: #047857; }
.code :deep(.j-num) { color: #b45309; }
.code :deep(.j-bool) { color: #0369a1; }
.code :deep(.j-nul) { color: #be123c; }
.hdrs { border: 1px solid #e8e8ed; border-radius: 9px; overflow: hidden; }
.hdr {
  display: grid;
  grid-template-columns: 160px 1fr;
  gap: 10px;
  padding: 5px 10px;
  font-size: 10px;
  border-bottom: 1px solid #f0f0f4;
}
.hdr:last-child { border-bottom: 0; }
.hdr .k { color: #9ca3af; font-family: Consolas, monospace; font-size: 9.5px; }
.hdr .v {
  color: #374151;
  font-family: Consolas, monospace;
  font-size: 9.5px;
  word-break: break-all;
}
.cempty {
  border: 1px dashed #e8e8ed;
  border-radius: 9px;
  background: #fcfcfe;
  padding: 12px;
  text-align: center;
  font-size: 10px;
  color: #9ca3af;
}
</style>

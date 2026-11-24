<script setup lang="ts">
/**
 * 调用链：按阶段分组 + 组内折叠。
 * 阶段分组只做归类，展开后才能看到单步的 arguments / result，几十步也不会撑破抽屉。
 */
import { computed, ref, watch } from 'vue'
import { fmtMs, phaseBuckets, type TurnStepVM } from './model'
import CopyBox from './CopyBox.vue'
import TraceIcon from './TraceIcon.vue'

const props = withDefaults(
  defineProps<{
    steps: TurnStepVM[]
    turnIndex: number
    /** 证据页用紧凑行，执行段用明细行 */
    compact?: boolean
  }>(),
  { compact: false },
)

const closedGroups = ref<Set<string>>(new Set())
const openSteps = ref<Set<number>>(new Set())
const filterMode = ref<'all' | 'warn' | 'slow'>('all')

watch(
  () => [props.turnIndex, props.steps.length],
  () => {
    const buckets = phaseBuckets(props.steps)
    const closed = new Set(buckets.map((group) => group.key))
    // 超过 12 步时先折叠：有异常就展开异常阶段，没有异常就展开步骤最多的阶段（与原型一致）
    if (props.steps.length > 12) {
      const warnGroups = buckets.filter((group) => group.warn > 0)
      if (warnGroups.length) {
        for (const group of warnGroups) closed.delete(group.key)
      } else if (buckets.length) {
        const largest = buckets.reduce((acc, group) => (group.n > acc.n ? group : acc), buckets[0])
        closed.delete(largest.key)
      }
    } else {
      closed.clear()
    }
    closedGroups.value = closed
    openSteps.value = new Set()
    filterMode.value = 'all'
  },
  { immediate: true },
)

// 过滤后自动展开有命中的分组，避免筛完仍是空面板
watch(filterMode, () => {
  closedGroups.value = new Set()
})

const buckets = computed(() => phaseBuckets(props.steps))
const totalMs = computed(() => props.steps.reduce((sum, step) => sum + Math.max(0, step.ms), 0))
const slowLimit = computed(() =>
  Math.max(50, Math.round((totalMs.value / Math.max(1, props.steps.length)) * 2)),
)
const warnCount = computed(() => props.steps.filter((step) => step.status === 'warn').length)
const slowCount = computed(() => props.steps.filter((step) => step.ms >= slowLimit.value).length)

const groups = computed(() =>
  phaseBuckets(props.steps)
    .map((group) => ({
      ...group,
      items: props.steps
        .map((step, index) => ({ step, index }))
        .filter((item) => item.step.phase === group.key)
        .filter((item) => {
          if (filterMode.value === 'warn') return item.step.status === 'warn'
          if (filterMode.value === 'slow') return item.step.ms >= slowLimit.value
          return true
        }),
    }))
    .filter((group) => group.items.length > 0),
)

const allClosed = computed(
  () => groups.value.length > 0 && groups.value.every((group) => closedGroups.value.has(group.key)),
)

function toggleAll(): void {
  if (allClosed.value) {
    closedGroups.value = new Set()
    return
  }
  closedGroups.value = new Set(groups.value.map((group) => group.key))
}

function toggleGroup(key: string): void {
  const next = new Set(closedGroups.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  closedGroups.value = next
}

function toggleStep(index: number): void {
  const next = new Set(openSteps.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  openSteps.value = next
}

function stepArgs(step: TurnStepVM, index: number): unknown {
  if (step.args !== null && step.args !== undefined) return step.args
  return { tool: step.tool, step: step.name, input: step.param || '', step_index: index + 1 }
}

function stepResult(step: TurnStepVM): unknown {
  if (step.result !== null && step.result !== undefined) return step.result
  return step.raw
}
</script>

<template>
  <div class="clist">
    <div v-if="!steps.length" class="cempty">
      <TraceIcon name="route" :size="14" />
      <p>本轮没有记录到工具调用。执行记录里 <code>tool_calls</code> 为空时，界面不会补造调用步骤。</p>
    </div>

    <template v-else>
      <template v-if="buckets.length >= 3">
        <div class="cdist">
          <i
            v-for="bucket in buckets"
            :key="bucket.key"
            :style="{
              width: `${(bucket.ms / Math.max(1, totalMs)) * 100}%`,
              background: bucket.warn ? '#f59e0b' : bucket.color,
            }"
            :title="`${bucket.label} · ${bucket.n} 步 · ${fmtMs(bucket.ms)}`"
          />
        </div>
        <div class="cdlegend">
          <span v-for="bucket in buckets" :key="bucket.key">
            <i :style="{ background: bucket.color }" />{{ bucket.label }}
            <b>{{ bucket.n }} 步 · {{ fmtMs(bucket.ms) }}</b>
          </span>
          <span v-if="warnCount">
            <i style="background: #f59e0b" />含异常
            <b>{{ buckets.filter((bucket) => bucket.warn).length }} 个阶段</b>
          </span>
        </div>
      </template>

      <div v-if="steps.length > 12" class="cbar">
        <button type="button" class="cchip" :class="{ on: filterMode === 'all' }" @click="filterMode = 'all'">
          全部<b>{{ steps.length }}</b>
        </button>
        <button
          v-if="warnCount"
          type="button"
          class="cchip warn"
          :class="{ on: filterMode === 'warn' }"
          title="只看未通过的步骤"
          @click="filterMode = 'warn'"
        >
          异常<b>{{ warnCount }}</b>
        </button>
        <button
          v-if="slowCount"
          type="button"
          class="cchip"
          :class="{ on: filterMode === 'slow' }"
          :title="`≥${slowLimit}ms（2× 平均）`"
          @click="filterMode = 'slow'"
        >
          慢步骤<b>{{ slowCount }}</b>
        </button>
        <span class="sp" />
        <button type="button" class="ctoggle" @click="toggleAll">
          {{ allClosed ? '展开全部' : '折叠全部' }}
        </button>
      </div>
    </template>

    <section v-for="group in groups" :key="group.key" class="cgrp" :class="{ closed: closedGroups.has(group.key) }">
      <button type="button" class="cgh" :aria-expanded="!closedGroups.has(group.key)" @click="toggleGroup(group.key)">
        <i :style="{ background: group.color }" />
        <span class="cgn">{{ group.label }}</span>
        <span class="cgnum">{{ group.items.length }} 步</span>
        <span v-if="group.warn" class="cgwarn">{{ group.warn }} 异常</span>
        <span class="sp" />
        <span class="cgms">{{ fmtMs(group.ms) }}</span>
        <TraceIcon name="chev" :size="11" class="cgchev" />
      </button>

      <div v-if="!closedGroups.has(group.key)" class="cgbody">
        <div v-for="item in group.items" :key="item.index" class="citem">
          <button
            type="button"
            class="crow"
            :class="{ warn: item.step.status === 'warn' }"
            :aria-expanded="openSteps.has(item.index)"
            @click="toggleStep(item.index)"
          >
            <span class="cix">{{ String(item.index + 1).padStart(2, '0') }}</span>
            <span class="cic" :class="{ warn: item.step.status === 'warn' }">
              <TraceIcon :name="item.step.status === 'warn' ? 'alert' : 'route'" :size="11" />
            </span>
            <span class="cnm">{{ item.step.tool }}</span>
            <span v-if="!compact && item.step.param" class="cpr">{{ item.step.param }}</span>
            <span class="sp" />
            <span class="cms" :class="{ warn: item.step.status === 'warn' }">{{ fmtMs(item.step.ms) }}</span>
            <span class="cgo">›</span>
          </button>

          <div v-if="openSteps.has(item.index)" class="cbody">
            <div class="ckv">
              <span><b>step</b>{{ item.step.name }}</span>
              <span><b>状态</b>{{ item.step.status === 'warn' ? '异常' : '成功' }}</span>
              <span v-if="item.step.raw.tool_call_id"><b>tool_call_id</b>{{ item.step.raw.tool_call_id }}</span>
              <span v-else><b>阶段</b>{{ item.step.phase }}</span>
            </div>
            <CopyBox title="arguments" :value="stepArgs(item.step, item.index)" :max-height="'26vh'" />
            <CopyBox title="result" :value="stepResult(item.step)" :max-height="'26vh'" />
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.clist { min-width: 0; }
.cdist { display: flex; height: 10px; border-radius: 5px; overflow: hidden; gap: 1px; background: #f1f2f6; margin: 9px 0 0; }
.cdist i { display: block; height: 100%; min-width: 3px; }
.cdlegend { display: flex; flex-wrap: wrap; gap: 4px 11px; margin: 7px 0 10px; font-size: 8.5px; color: #9ca3af; }
.cdlegend span { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; }
.cdlegend i { display: inline-block; width: 6px; height: 6px; border-radius: 2px; }
.cdlegend b { font-family: Consolas, monospace; font-weight: 500; color: #6b7280; }
.cbar { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; margin: 0 0 8px; }
.cbar .sp { flex: 1; }
.cchip {
  border: 1px solid #eceef3;
  background: #fff;
  border-radius: 7px;
  padding: 2px 7px;
  font: inherit;
  font-size: 9.5px;
  color: #6b7280;
  cursor: pointer;
  white-space: nowrap;
}
.cchip b { font-family: Consolas, monospace; font-size: 9px; font-weight: 600; margin-left: 4px; color: #9ca3af; }
.cchip:hover { border-color: #c7d2fe; color: #4f46e5; }
.cchip.on { background: #eef2ff; border-color: #c7d2fe; color: #4338ca; font-weight: 500; }
.cchip.on b { color: #4338ca; }
.cchip.warn.on { background: #fffbeb; border-color: #fde68a; color: #b45309; }
.cchip.warn.on b { color: #b45309; }
.ctoggle {
  border: 0;
  background: none;
  font: inherit;
  font-size: 9.5px;
  color: #4f46e5;
  cursor: pointer;
  padding: 2px 0;
  white-space: nowrap;
}
.ctoggle:hover { text-decoration: underline; }
.cempty {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  border: 1px dashed #e8e8ed;
  border-radius: 9px;
  background: #fcfcfe;
  padding: 10px 12px;
  color: #9ca3af;
  font-size: 10px;
  line-height: 1.7;
}
.cempty p { margin: 0; }
.cempty code { font-family: Consolas, monospace; color: #6b7280; }
.cgrp { border: 1px solid #eceef3; border-radius: 10px; overflow: hidden; margin-bottom: 7px; background: #fff; }
.cgh {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 10px;
  background: #fafbfd;
  border: 0;
  border-bottom: 1px solid #f0f2f6;
  font-size: 10px;
  color: #374151;
  cursor: pointer;
  text-align: left;
}
.cgrp.closed .cgh { border-bottom: 0; }
.cgh:hover { background: #f5f6fb; }
.cgh i { width: 6px; height: 6px; border-radius: 50%; flex: none; }
.cgn { font-weight: 600; }
.cgnum { font-family: Consolas, monospace; font-size: 9px; color: #9ca3af; }
.cgwarn { font-size: 9px; color: #b45309; background: #fffbeb; border: 1px solid #fde68a; border-radius: 999px; padding: 0 6px; }
.cgh .sp { flex: 1; }
.cgms { font-family: Consolas, monospace; font-size: 9px; color: #9ca3af; }
.cgchev { color: #b0b6c1; transition: transform 0.15s; }
.cgrp.closed .cgchev { transform: rotate(-90deg); }
.cgbody { padding: 3px 6px 6px; }
.citem + .citem { border-top: 1px dashed #f1f2f6; }
.crow {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 5px 4px;
  background: none;
  border: 0;
  font-size: 10px;
  color: #374151;
  cursor: pointer;
  text-align: left;
  min-width: 0;
}
.crow:hover { background: #fbfbfe; }
.cix { font-family: Consolas, monospace; font-size: 8.5px; color: #b0b6c1; flex: none; }
.cic { color: #6366f1; flex: none; display: inline-flex; }
.cic.warn { color: #d97706; }
.cnm {
  font-family: Consolas, monospace;
  font-size: 9.5px;
  color: #374151;
  background: #f4f4f7;
  border-radius: 5px;
  padding: 1px 5px;
  flex: none;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cpr {
  min-width: 0;
  color: #9ca3af;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.crow .sp { flex: 1; }
.cms { font-family: Consolas, monospace; font-size: 9px; color: #6b7280; flex: none; }
.cms.warn { color: #b45309; }
.cgo { color: #c3c9d4; flex: none; }
.cbody {
  border: 1px solid #eef0f5;
  border-radius: 8px;
  background: #fcfcfe;
  padding: 6px 8px 8px;
  margin: 0 2px 6px;
}
.ckv { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 9px; color: #6b7280; margin-bottom: 2px; }
.ckv span { display: inline-flex; gap: 4px; min-width: 0; }
.ckv b { font-weight: 500; color: #9ca3af; }
</style>

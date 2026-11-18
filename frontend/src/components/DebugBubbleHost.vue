<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import AnimatedNumber from './AnimatedNumber.vue'
import { router } from '../router'
import { useDebugStore } from '../stores/debug'
import type { DebugTask } from '../stores/debug'

/**
 * 单条调试底部胶囊提示（定稿设计：底部居中胶囊 + 分级堆叠）。
 * - 用户仍停留在发起页时不展示「调试中」胶囊（发起页按钮态负责提示），
 *   切走页面后才由胶囊接管运行进度
 * - 1 条结果：基准单条胶囊
 * - 2 条：直接垂直堆叠
 * - ≥3 条：顶部「+N 条更早结果」虚线胶囊，下方最多平铺 2 条，点击可展开全部
 * - 运行中的胶囊始终钉在堆叠最底部，完成后原位变为结果胶囊；
 *   带「查看结果 →」（跳用例详情）与 ×（收起运行提示，调试继续、完成后仍弹结果）
 * - 结果/失败胶囊常驻，不自动消失；× 手动关闭，「查看结果」跳转后同样关闭该胶囊
 */
const debug = useDebugStore()

const expanded = ref(false)

const now = ref(Date.now())
let timer: number | null = null

onMounted(() => {
  timer = window.setInterval(() => {
    now.value = Date.now()
  }, 1000)
})

onBeforeUnmount(() => {
  if (timer !== null) window.clearInterval(timer)
})

/**
 * 展示顺序：已完成在上（越新越靠下），运行中钉在最底部。
 * 运行中的任务只在用户已离开发起页时才展示 —— 留在发起页时由
 * 按钮态 + 完成后 toast/跳转负责提示，回到发起页时胶囊自动隐藏。
 */
const ordered = computed<DebugTask[]>(() => {
  const finished = debug.tasks
    .filter((task) => task.status !== 'running')
    .slice()
    .sort((a, b) => (a.finishedAt ?? 0) - (b.finishedAt ?? 0))
  const running = debug.tasks
    .filter(
      (task) =>
        task.status === 'running' &&
        !task.runningDismissed &&
        !debug.isOnOrigin(task),
    )
    .slice()
    .sort((a, b) => a.startedAt - b.startedAt)
  return [...finished, ...running]
})

const collapsed = computed(() => ordered.value.length >= 3 && !expanded.value)
const hiddenCount = computed(() => Math.max(0, ordered.value.length - 2))
const visible = computed<DebugTask[]>(() =>
  collapsed.value ? ordered.value.slice(-2) : ordered.value,
)

/** 关闭/收起动画进行中的任务：180ms 淡出后再执行移除，避免胶囊瞬间消失 */
const leavingIds = ref<Set<number>>(new Set())

function markLeaving(id: number, action: () => void): void {
  if (leavingIds.value.has(id)) return
  leavingIds.value = new Set(leavingIds.value).add(id)
  window.setTimeout(() => {
    action()
    const next = new Set(leavingIds.value)
    next.delete(id)
    leavingIds.value = next
  }, 180)
}

/** 关闭提示（结果态 ×、「查看结果」跳转后） */
function closeTask(task: DebugTask): void {
  markLeaving(task.id, () => debug.remove(task.id))
}

/** 收起运行提示：调试继续后台执行，完成后仍弹结果胶囊 */
function dismissRunning(task: DebugTask): void {
  markLeaving(task.id, () => debug.dismissRunning(task.id))
}

/** 胶囊状态类：运行态流光扫 / 完成态底色 + 一次性光晕 / 关闭淡出 */
function pillClass(task: DebugTask): string[] {
  const classes: string[] = []
  if (task.status === 'running') {
    classes.push('bubble--running')
  } else {
    const bad = task.passed === false || task.status === 'failed'
    classes.push(bad ? 'bubble--bad' : 'bubble--ok')
    classes.push(bad ? 'bubble--flash-bad' : 'bubble--flash-ok')
  }
  if (leavingIds.value.has(task.id)) classes.push('bubble--leaving')
  return classes
}

function elapsedText(task: DebugTask): string {
  const seconds = Math.max(0, Math.round((now.value - task.startedAt) / 1000))
  return `已用时 ${seconds}s`
}

function durationText(task: DebugTask): string {
  const seconds = Math.round((task.durationMs ?? 0) / 1000)
  return `用时 ${seconds}s`
}

/** 结果态文案（分数单独滚动渲染）：通过 / 未通过；pass_flag 缺失时退化为执行状态 */
function verdictText(task: DebugTask): string {
  if (task.passed === true) return '通过'
  if (task.passed === false) return '未通过'
  return task.executionStatus ? statusText(task.executionStatus) : '已完成'
}

function statusText(status: string): string {
  const labels: Record<string, string> = {
    PASS: '通过',
    FAIL: '失败',
    ERROR: '异常',
    MAX_TURNS_EXCEEDED: '轮次超限',
    REPETITION_ABORT: '复读熔断',
    ABORTED: '已终止',
  }
  return labels[status] ?? status
}

/** 完成态追加的自检反馈标签（未开启失败追问自检时为空） */
function selfCheckText(task: DebugTask): string | null {
  if (task.selfCheckStatus === 'DONE') return '已追问自检'
  if (task.selfCheckStatus === 'SKIPPED') return '自检未触发'
  if (task.selfCheckStatus === 'ERROR') return '自检失败'
  return null
}

function truncate(text: string, max = 36): string {
  return text.length > max ? `${text.slice(0, max)}…` : text
}

/** 运行中胶囊的「查看结果」：结果尚未产生，先跳转该用例详情页 */
function openCase(task: DebugTask): void {
  void router.push({ name: 'case-detail', params: { id: String(task.caseId) } })
}

function viewResult(task: DebugTask): void {
  if (task.executionId === null) return
  // 「查看结果」与 × 等价：跳转 trace 的同时关闭该胶囊（带淡出）
  void router.push({
    name: 'trace',
    query: { execution: String(task.executionId), from: 'case' },
  })
  closeTask(task)
}
</script>

<template>
  <div
    class="pointer-events-none fixed inset-x-0 bottom-5 z-[90] flex flex-col items-center gap-2 px-4"
  >
    <!-- ≥3 条：溢出计数胶囊（虚线），点击展开/收起全部 -->
    <div v-if="ordered.length >= 3" key="debug-overflow" class="bubble bubble--ghost">
      <template v-if="expanded">
        <svg
          class="h-3.5 w-3.5 shrink-0 text-slate-400"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        >
          <path stroke-linecap="round" stroke-linejoin="round" d="M4 6h16M4 12h16M4 18h10" />
        </svg>
        <span class="bubble-title">共 {{ ordered.length }} 条调试提示</span>
        <span class="bubble-sep"></span>
        <button class="bubble-cta bubble-cta--ghost" @click="expanded = false">收起列表</button>
      </template>
      <template v-else>
        <svg
          class="h-3.5 w-3.5 shrink-0 text-slate-400"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2.4"
        >
          <path stroke-linecap="round" stroke-linejoin="round" d="m6 15 6-6 6 6" />
        </svg>
        <span class="bubble-title">+{{ hiddenCount }} 条更早结果</span>
        <span class="bubble-sep"></span>
        <button class="bubble-cta bubble-cta--ghost" @click="expanded = true">查看全部</button>
      </template>
    </div>

    <div v-for="task in visible" :key="task.id" class="bubble" :class="pillClass(task)">
      <!-- 调试中 -->
      <template v-if="task.status === 'running'">
        <span class="bubble-icon bubble-icon--run">
          <svg
            class="h-3.5 w-3.5 bubble-spin"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="3"
          >
            <path stroke-linecap="round" d="M12 3a9 9 0 1 0 9 9" />
          </svg>
        </span>
        <span class="bubble-title">{{ task.caseCode }} 调试中…</span>
        <span class="bubble-sub">{{ elapsedText(task) }}</span>
        <span class="bubble-sep"></span>
        <button class="bubble-cta" @click="openCase(task)">查看结果 →</button>
        <button
          class="bubble-close"
          aria-label="关闭运行提示"
          @click="dismissRunning(task)"
        >
          <svg
            class="h-3.5 w-3.5"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          >
            <path stroke-linecap="round" d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>
      </template>

      <!-- 调试失败（请求异常） -->
      <template v-else-if="task.status === 'failed'">
        <span class="bubble-icon bubble-icon--in bubble-icon--bad">
          <svg
            class="h-3.5 w-3.5"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2.6"
          >
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 6l12 12M18 6 6 18" />
          </svg>
        </span>
        <span class="bubble-title">{{ task.caseCode }} 调试失败</span>
        <span class="bubble-sub" :title="task.errorText ?? undefined">
          {{ truncate(task.errorText ?? '请求异常') }}
        </span>
        <span class="bubble-sep bubble-in"></span>
        <button
          class="bubble-close bubble-in"
          aria-label="关闭提示"
          @click="closeTask(task)"
        >
          <svg
            class="h-3.5 w-3.5"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          >
            <path stroke-linecap="round" d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>
      </template>

      <!-- 调试完成（含未通过） -->
      <template v-else>
        <span
          class="bubble-icon bubble-icon--in"
          :class="task.passed === false ? 'bubble-icon--bad' : 'bubble-icon--ok'"
        >
          <svg
            v-if="task.passed === false"
            class="h-3.5 w-3.5"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2.6"
          >
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 6l12 12M18 6 6 18" />
          </svg>
          <svg
            v-else
            class="h-3.5 w-3.5"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2.6"
          >
            <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
          </svg>
        </span>
        <span class="bubble-title">
          {{ task.caseCode }} 调试完成 ·
          <span
            class="font-bold"
            :class="task.passed === false ? 'text-rose-600' : 'text-emerald-600'"
          >
            {{ verdictText(task) }}<template v-if="task.totalScore !== null">
              · <AnimatedNumber :value="task.totalScore" /> 分</template>
          </span>
        </span>
        <span
          v-if="selfCheckText(task)"
          class="bubble-chip"
          :class="
            task.selfCheckStatus === 'DONE'
              ? 'bubble-chip--ok'
              : task.selfCheckStatus === 'ERROR'
                ? 'bubble-chip--bad'
                : ''
          "
        >
          {{ selfCheckText(task) }}
        </span>
        <span class="bubble-sub">{{ durationText(task) }}</span>
        <span class="bubble-sep bubble-in"></span>
        <button
          class="bubble-cta bubble-in"
          style="animation-delay: 40ms"
          @click="viewResult(task)"
        >
          查看结果 →
        </button>
        <button
          class="bubble-close bubble-in"
          style="animation-delay: 80ms"
          aria-label="关闭提示"
          @click="closeTask(task)"
        >
          <svg
            class="h-3.5 w-3.5"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          >
            <path stroke-linecap="round" d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.bubble {
  pointer-events: auto;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  height: 44px;
  padding: 0 8px 0 14px;
  border-radius: 9999px;
  border: 1px solid #e8edf4;
  background: #fff;
  box-shadow:
    0 18px 34px -18px rgba(15, 23, 42, 0.4),
    0 2px 6px -3px rgba(15, 23, 42, 0.1);
  /* 纯 CSS 关键帧入场：元素插入即播放，不依赖 Vue 的过渡类轮换，
     在后台标签页 / rAF 被节流的环境下也能正常显示 */
  animation: bubble-rise 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
  position: relative;
  overflow: hidden;
  transition:
    background-color 0.32s ease,
    border-color 0.32s ease;
}

/* 内容层级压在流光 / 底色之上 */
.bubble > * {
  position: relative;
  z-index: 1;
}

@keyframes bubble-rise {
  from {
    opacity: 0;
    transform: translateY(14px) scale(0.96);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

/* 运行态（方案 A · 流光扫）：淡靛蓝高光缓慢扫过胶囊表面，扫完停顿一拍再循环 */
.bubble--running::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  pointer-events: none;
  background: linear-gradient(
    100deg,
    transparent 22%,
    rgba(99, 102, 241, 0.1) 42%,
    rgba(99, 102, 241, 0.2) 50%,
    rgba(99, 102, 241, 0.07) 58%,
    transparent 76%
  );
  background-size: 240% 100%;
  background-repeat: no-repeat;
  animation: bubble-sheen 8s ease-in-out infinite;
}

@keyframes bubble-sheen {
  0% {
    background-position: 175% 0;
  }
  62% {
    background-position: -85% 0;
  }
  100% {
    background-position: -85% 0;
  }
}

/* 运行图标：细弧慢转 1.4s / 圈 */
.bubble-spin {
  animation: bubble-rot 1.4s linear infinite;
}

@keyframes bubble-rot {
  to {
    transform: rotate(360deg);
  }
}

/* 完成态：底色过渡 + 一次性光晕（只播 1 次，之后完全静止） */
.bubble--ok {
  background: #f2fdf7;
  border-color: #d1fae5;
}

.bubble--bad {
  background: #fff6f7;
  border-color: #fecdd3;
}

.bubble--flash-ok {
  animation: bubble-flash-ok 0.72s cubic-bezier(0.22, 1, 0.36, 1);
}

.bubble--flash-bad {
  animation: bubble-flash-bad 0.72s cubic-bezier(0.22, 1, 0.36, 1);
}

@keyframes bubble-flash-ok {
  0% {
    box-shadow:
      0 18px 34px -18px rgba(15, 23, 42, 0.4),
      0 2px 6px -3px rgba(15, 23, 42, 0.1),
      0 0 0 0 rgba(16, 185, 129, 0.38);
  }
  35% {
    box-shadow:
      0 18px 34px -18px rgba(15, 23, 42, 0.4),
      0 2px 6px -3px rgba(15, 23, 42, 0.1),
      0 0 0 9px rgba(16, 185, 129, 0);
    transform: translateY(-2px);
  }
  100% {
    box-shadow:
      0 18px 34px -18px rgba(15, 23, 42, 0.4),
      0 2px 6px -3px rgba(15, 23, 42, 0.1),
      0 0 0 0 rgba(16, 185, 129, 0);
  }
}

@keyframes bubble-flash-bad {
  0% {
    box-shadow:
      0 18px 34px -18px rgba(15, 23, 42, 0.4),
      0 2px 6px -3px rgba(15, 23, 42, 0.1),
      0 0 0 0 rgba(225, 29, 72, 0.38);
  }
  35% {
    box-shadow:
      0 18px 34px -18px rgba(15, 23, 42, 0.4),
      0 2px 6px -3px rgba(15, 23, 42, 0.1),
      0 0 0 9px rgba(225, 29, 72, 0);
    transform: translateY(-2px);
  }
  100% {
    box-shadow:
      0 18px 34px -18px rgba(15, 23, 42, 0.4),
      0 2px 6px -3px rgba(15, 23, 42, 0.1),
      0 0 0 0 rgba(225, 29, 72, 0);
  }
}

/* 完成瞬间：图标弹性弹出、操作区错峰淡入 */
.bubble-icon--in {
  animation: bubble-icon-pop 0.26s cubic-bezier(0.34, 1.56, 0.64, 1);
}

@keyframes bubble-icon-pop {
  0% {
    transform: scale(0.4) rotate(-30deg);
    opacity: 0;
  }
  60% {
    transform: scale(1.12) rotate(2deg);
    opacity: 1;
  }
  100% {
    transform: scale(1) rotate(0);
  }
}

.bubble-in {
  animation: bubble-fade-up 0.22s ease-out both;
}

@keyframes bubble-fade-up {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

/* 关闭 / 收起：淡出下沉 180ms，结束后从 store 移除 */
.bubble--leaving {
  pointer-events: none;
  animation: bubble-out 0.18s ease-in forwards;
}

@keyframes bubble-out {
  to {
    opacity: 0;
    transform: scale(0.94) translateY(8px);
  }
}

/*
 * 调试气泡是临时状态提示，动效用于表达「正在执行 / 刚刚完成」。
 * 全局减少动效规则会把所有动画压到 0.001ms，这里对气泡单独豁免；
 * 如需严格遵循系统设置，删除本段即可恢复全局行为。
 */
@media (prefers-reduced-motion: reduce) {
  .bubble {
    animation-duration: 0.3s !important;
  }

  .bubble--running::after {
    animation-duration: 8s !important;
    animation-iteration-count: infinite !important;
  }

  .bubble--flash-ok,
  .bubble--flash-bad {
    animation-duration: 0.72s !important;
  }

  .bubble-spin {
    animation-duration: 1.4s !important;
    animation-iteration-count: infinite !important;
  }

  .bubble-icon--in {
    animation-duration: 0.26s !important;
  }

  .bubble-in {
    animation-duration: 0.22s !important;
  }

  .bubble--leaving {
    animation-duration: 0.18s !important;
  }
}

.bubble--ghost {
  border-style: dashed;
  border-color: #dbe2ec;
  background: #f8fafc;
  padding-right: 10px;
}

.bubble-icon {
  display: flex;
  height: 22px;
  width: 22px;
  flex: none;
  align-items: center;
  justify-content: center;
  border-radius: 9999px;
}

.bubble-icon--ok {
  background: #dcfce7;
  color: #059669;
}

.bubble-icon--bad {
  background: #ffe4e6;
  color: #e11d48;
}

.bubble-icon--run {
  background: #eef2ff;
  color: #4f46e5;
}

.bubble-chip {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  border-radius: 9999px;
  border: 1px solid #e2e8f0;
  background: #f1f5f9;
  color: #475569;
  font-size: 10px;
  font-weight: 600;
  white-space: nowrap;
}

.bubble-chip--ok {
  border-color: #a7f3d0;
  background: #ecfdf5;
  color: #047857;
}

.bubble-chip--bad {
  border-color: #fecdd3;
  background: #fff1f2;
  color: #be123c;
}

.bubble-title {
  font-size: 11.5px;
  font-weight: 600;
  color: #334155;
  white-space: nowrap;
}

.bubble-sub {
  font-size: 10.5px;
  color: #94a3b8;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.bubble-sep {
  width: 1px;
  height: 20px;
  flex: none;
  background: #eef0f5;
}

.bubble-cta {
  appearance: none;
  cursor: pointer;
  border: 0;
  height: 30px;
  padding: 0 12px;
  border-radius: 9999px;
  background: #eef2ff;
  color: #4f46e5;
  font-size: 11px;
  font-weight: 700;
  white-space: nowrap;
  transition: background 0.15s ease;
}

.bubble-cta:hover {
  background: #e0e7ff;
}

.bubble-cta--ghost {
  background: #f1f5f9;
  color: #475569;
}

.bubble-cta--ghost:hover {
  background: #e2e8f0;
}

.bubble-close {
  appearance: none;
  cursor: pointer;
  border: 0;
  background: transparent;
  color: #94a3b8;
  height: 26px;
  width: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  transition: all 0.15s ease;
}

.bubble-close:hover {
  background: #f1f5f9;
  color: #475569;
}
</style>

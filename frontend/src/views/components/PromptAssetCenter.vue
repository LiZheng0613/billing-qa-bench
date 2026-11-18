<script setup lang="ts">
import { computed } from 'vue'
import EmptyState from '../../components/EmptyState.vue'
import StatusPill from '../../components/StatusPill.vue'
import type { PromptTemplate } from '../../types'
import { formatDateTime, statusLabel, templateTypeLabel } from '../../utils/format'

const props = defineProps<{ template: PromptTemplate | null; loading: boolean }>()
const emit = defineEmits<{
  (e: 'create-version'): void
  (e: 'view-usage'): void
  (e: 'sandbox', version?: number): void
  (e: 'edit-meta'): void
  (e: 'delete'): void
}>()

const versions = computed(() =>
  [...(props.template?.versions ?? [])].sort((a, b) => b.version - a.version),
)
</script>

<template>
  <section class="card flex min-h-[420px] flex-col overflow-hidden">
    <div v-if="loading" class="py-20 text-center text-sm text-slate-500">加载模板详情…</div>
    <EmptyState
      v-else-if="!template"
      title="从左侧选择一个模板"
      description="查看版本历史、引用影响，并在沙盒中试用 Prompt。"
    />
    <template v-else>
      <header class="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 px-5 py-4">
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-2">
            <h2 class="text-base font-semibold text-slate-900">{{ template.name }}</h2>
            <span class="rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-700">
              {{ templateTypeLabel(template.template_type) }}
            </span>
            <span v-if="template.is_builtin" class="tag border border-indigo-200 bg-indigo-50 text-indigo-600">内置</span>
            <StatusPill :status="template.is_active ? 'ACTIVE' : 'DISABLED'" size="sm" />
          </div>
          <p class="mt-1 font-mono text-[11px] text-slate-500">{{ template.code }}</p>
          <p v-if="template.description" class="mt-1 text-xs text-slate-500">{{ template.description }}</p>
        </div>
        <div class="flex flex-wrap gap-2">
          <button class="btn-primary btn-xs" @click="emit('sandbox')">沙盒试用</button>
          <button class="btn-ghost btn-xs" @click="emit('create-version')">发布新版本</button>
          <button class="btn-ghost btn-xs" @click="emit('view-usage')">引用影响</button>
          <button class="btn-ghost btn-xs" @click="emit('edit-meta')">编辑信息</button>
          <button class="btn-danger btn-xs" :disabled="template.is_builtin" @click="emit('delete')">删除</button>
        </div>
      </header>

      <div class="grid grid-cols-2 gap-3 border-b border-slate-200 px-5 py-3 sm:grid-cols-4">
        <div>
          <p class="text-[10px] uppercase tracking-wider text-slate-500">当前版本</p>
          <p class="mt-0.5 text-lg font-semibold text-indigo-600">v{{ template.current_version }}</p>
        </div>
        <div>
          <p class="text-[10px] uppercase tracking-wider text-slate-500">引用用例</p>
          <p class="mt-0.5 text-lg font-semibold text-slate-900">{{ template.ref_count }}</p>
        </div>
        <div>
          <p class="text-[10px] uppercase tracking-wider text-slate-500">创建时间</p>
          <p class="mt-0.5 text-xs text-slate-700">{{ formatDateTime(template.created_at) }}</p>
        </div>
        <div>
          <p class="text-[10px] uppercase tracking-wider text-slate-500">更新时间</p>
          <p class="mt-0.5 text-xs text-slate-700">{{ formatDateTime(template.updated_at) }}</p>
        </div>
      </div>

      <div class="flex-1 overflow-y-auto px-5 py-4">
        <h3 class="panel-title mb-2">版本历史（{{ versions.length }}）</h3>
        <div class="space-y-2">
          <div
            v-for="version in versions"
            :key="version.id"
            class="rounded-xl border border-slate-200 bg-slate-50"
          >
            <div class="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-mono text-xs font-semibold text-indigo-600">v{{ version.version }}</span>
                <span v-if="version.version === template.current_version" class="tag">当前</span>
                <span class="text-[11px] text-slate-600">{{ version.changelog || '无变更说明' }}</span>
                <span class="text-[10px] text-slate-500">{{ version.created_by || 'system' }} · {{ formatDateTime(version.created_at) }}</span>
              </div>
              <button class="btn-ghost btn-xs" @click="emit('sandbox', version.version)">用此版本试用</button>
            </div>
            <div v-if="version.variables?.length" class="flex flex-wrap gap-1 px-3 pb-2">
              <span v-for="variable in version.variables" :key="variable" class="tag font-mono">
                {{ variable }}
              </span>
            </div>
            <details class="border-t border-slate-200">
              <summary class="cursor-pointer px-3 py-1.5 text-[11px] text-slate-500 hover:text-slate-700">
                查看模板内容
              </summary>
              <pre class="mono max-h-64 overflow-auto whitespace-pre-wrap border-t border-slate-200 p-3 text-slate-700">{{ version.content }}</pre>
            </details>
          </div>
          <EmptyState v-if="versions.length === 0" compact title="暂无版本内容" />
        </div>

        <p class="mt-4 text-[11px] leading-relaxed text-slate-500">
          模板类型：{{ statusLabel(template.template_type) }}。模拟器模板用于动态多轮的用户代理，
          裁判模板用于结构化评分与红线判定，编写模板用于生成用例草稿，
          自检模板用于 LLM 裁决失败后在同一会话追问被测 Agent 自查原因。
        </p>
      </div>
    </template>
  </section>
</template>

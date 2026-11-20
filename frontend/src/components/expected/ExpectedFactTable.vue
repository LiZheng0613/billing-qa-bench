<script setup lang="ts">
import StatusPill from '../StatusPill.vue'
import { bindingModeLabel, factKindLabel, type FactRow } from '../../utils/expectation'

withDefaults(
  defineProps<{
    facts: FactRow[]
    title?: string
  }>(),
  { title: '判分事实对照' },
)
</script>

<template>
  <div v-if="facts.length">
    <h4 class="text-[11px] font-semibold text-slate-600">{{ title }}</h4>
    <div class="mt-1.5 overflow-hidden rounded-xl border border-line">
      <table class="table-shell">
        <thead>
          <tr>
            <th class="th">事实</th>
            <th class="th">类型</th>
            <th class="th">期望</th>
            <th class="th">实际</th>
            <th class="th">闸门</th>
            <th class="th">结果</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="fact in facts" :key="fact.id">
            <td class="td">
              <p class="text-slate-800">{{ fact.label }}</p>
              <p class="font-mono text-[10px] text-slate-500">{{ fact.id }}</p>
              <p v-if="fact.mode || fact.boundName" class="mt-0.5 text-[10px] text-slate-500">
                {{ bindingModeLabel(fact.mode) }}
                <span v-if="fact.boundName">· {{ fact.boundName }}</span>
              </p>
            </td>
            <td class="td text-xs">{{ factKindLabel(fact.kind) }}</td>
            <td class="td max-w-[220px] break-words text-xs">{{ fact.expectedText }}</td>
            <td class="td max-w-[220px] break-words text-xs">{{ fact.actualText }}</td>
            <td class="td text-[11px] text-slate-500">
              {{ fact.section === 'must' ? 'Must' : 'May' }}
            </td>
            <td class="td">
              <StatusPill
                v-if="fact.passed !== null"
                :status="fact.passed ? 'PASS' : 'FAIL'"
                :label="fact.passed ? '命中' : '未命中'"
                size="sm"
              />
              <span v-else class="tag border-amber-200 text-amber-700">未判定</span>
              <p
                v-if="fact.reason"
                class="mt-1 text-[10px]"
                :class="fact.passed === false ? 'text-rose-600' : 'text-slate-500'"
              >
                {{ fact.reason }}
              </p>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

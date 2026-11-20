<script setup lang="ts">
import StatusPill from '../StatusPill.vue'
import { bindingModeLabel, type RuleRow } from '../../utils/expectation'

defineProps<{
  rows: RuleRow[]
  title?: string
}>()
</script>

<template>
  <div v-if="rows.length">
    <h4 class="text-[11px] font-semibold text-slate-600">{{ title ?? '规则校验' }}</h4>
    <div class="mt-1.5 overflow-hidden rounded-xl border border-line">
      <table class="table-shell">
        <thead>
          <tr>
            <th class="th">校验项</th>
            <th class="th">期望</th>
            <th class="th">实际</th>
            <th class="th">结果</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.id">
            <td class="td">
              <p class="text-slate-800">{{ row.label }}</p>
              <p v-if="row.mode || row.boundName" class="mt-0.5 text-[10px] text-slate-500">
                {{ bindingModeLabel(row.mode) }}
                <span v-if="row.boundName">· {{ row.boundName }}</span>
              </p>
              <p v-if="row.evidence" class="mt-0.5 text-[10px] text-slate-500">
                {{ row.evidence }}
              </p>
            </td>
            <td class="td max-w-[220px] break-words text-xs">{{ row.expectedText }}</td>
            <td class="td max-w-[220px] break-words text-xs">{{ row.actualText }}</td>
            <td class="td">
              <StatusPill
                v-if="row.passed !== null"
                :status="row.passed ? 'PASS' : 'FAIL'"
                :label="row.passed ? '通过' : '未通过'"
                size="sm"
              />
              <span v-else class="tag border-amber-200 text-amber-700">未校验</span>
              <p v-if="row.reason" class="mt-1 text-[10px]" :class="row.passed === false ? 'text-rose-600' : 'text-slate-500'">{{ row.reason }}</p>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

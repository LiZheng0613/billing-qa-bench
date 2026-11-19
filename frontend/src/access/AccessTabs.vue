<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()
const tabs = [
  { name: 'access-users', label: '用户管理', tab: 'users' },
  { name: 'access-roles', label: '角色权限', tab: 'roles' },
  { name: 'access-audit', label: '审计日志', tab: 'audit' },
]
</script>

<template>
  <div class="inline-flex items-center gap-0.5 rounded-xl bg-[#ececf1] p-0.5">
    <RouterLink
      v-for="item in tabs"
      :key="item.name"
      :to="{ name: item.name }"
      class="inline-flex h-7 items-center gap-1.5 rounded-lg px-3 text-[11.5px] font-semibold transition"
      :class="route.meta.accessTab === item.tab ? 'bg-white text-ink shadow-sm' : 'text-slate-500 hover:text-ink'"
    >
      {{ item.label }}
    </RouterLink>
  </div>
</template>

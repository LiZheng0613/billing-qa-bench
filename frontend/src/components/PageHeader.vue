<script setup lang="ts">
withDefaults(
  defineProps<{
    eyebrow?: string
    title?: string
    description?: string
  }>(),
  { eyebrow: '', title: '', description: '' },
)
</script>

<template>
  <header class="flex flex-wrap items-end justify-between gap-4 pb-1">
    <div class="min-w-0">
      <p
        v-if="eyebrow"
        class="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-600"
      >
        <span class="h-1 w-1 rounded-full bg-brand" aria-hidden="true" />
        {{ eyebrow }}
      </p>
      <slot name="title">
        <h1 class="page-title" :class="eyebrow ? 'mt-2' : ''">{{ title }}</h1>
      </slot>
      <p v-if="description" class="page-sub">{{ description }}</p>
      <slot name="sub" />
    </div>
    <div v-if="$slots.actions" class="flex flex-wrap items-center gap-2">
      <slot name="actions" />
    </div>
    <div v-if="$slots.meta" class="flex flex-wrap items-center gap-2">
      <slot name="meta" />
    </div>
  </header>
</template>

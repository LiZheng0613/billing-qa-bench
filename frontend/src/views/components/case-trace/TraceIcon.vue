<script setup lang="ts">
/** 线性图标（与原型同一套 24×24 path），避免为每个图标写一段 SVG。 */
const PATHS: Record<string, string[]> = {
  check: ['M20 6 9 17l-5-5'],
  alert: ['M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z', 'M12 9v4', 'M12 17h.01'],
  x: ['M6 6l12 12M18 6 6 18'],
  chev: ['m6 9 6 6 6-6'],
  clock: ['M12 7v5l3 2', 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z'],
  bolt: ['M13 2 4.5 13.5H11l-1 8.5L19 10h-6.5L13 2Z'],
  hash: ['M4 9h16M4 15h16M10 3 8 21M16 3l-2 18'],
  db: ['M4 5c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3Z', 'M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5', 'M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3'],
  download: ['M12 3v12', 'm7 10 5 5 5-5', 'M4 21h16'],
  file: ['M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7l-5-5Z', 'M14 2v5h5'],
  pin: ['M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0Z', 'M12 7a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z'],
  box: ['M21 8 12 3 3 8v8l9 5 9-5V8Z', 'm3 8 9 5 9-5', 'M12 21V13'],
  bot: ['M4 7h16v12H4z', 'M12 3v4', 'M9 12h.01M15 12h.01', 'M9 16h6'],
  copy: ['M9 9h12v12H9z', 'M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1'],
  code: ['m8 8-5 4 5 4M16 8l5 4-5 4'],
  spark: ['M12 3v4M12 17v4M3 12h4M17 12h4M6.3 6.3l2.8 2.8M14.9 14.9l2.8 2.8M17.7 6.3l-2.8 2.8M9.1 14.9l-2.8 2.8'],
  search: ['M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14Z', 'm20 20-3.5-3.5'],
  layers: ['m12 3 9 5-9 5-9-5 9-5Z', 'm3 13 9 5 9-5'],
  grid: ['M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z'],
  user: ['M12 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z', 'M4 21a8 8 0 0 1 16 0'],
  chat: ['M21 12a8 8 0 0 1-8 8H7l-4 3 1.5-5A8 8 0 1 1 21 12Z'],
  columns: ['M3 4h7v16H3zM14 4h7v16h-7z'],
  list: ['M8 6h13M8 12h13M8 18h13', 'M3 6h.01M3 12h.01M3 18h.01'],
  info: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z', 'M12 11v5', 'M12 8h.01'],
  scale: ['M12 3v18M7 6h10', 'M5 9 2 15h6L5 9ZM19 9l-3 6h6l-3-6Z', 'M8 21h8'],
  refresh: ['M20 11a8 8 0 0 0-14-4L3 10', 'M3 5v5h5', 'M4 13a8 8 0 0 0 14 4l3-3', 'M21 19v-5h-5'],
  route: ['M5 4a2 2 0 1 0 0 4 2 2 0 0 0 0-4Z', 'M19 16a2 2 0 1 0 0 4 2 2 0 0 0 0-4Z', 'M7 6h5a3 3 0 0 1 3 3v6a3 3 0 0 0 3 3h-1', 'M4 8v8a3 3 0 0 0 3 3h3'],
  shield: ['M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3Z', 'm9 12 2 2 4-4'],
  target: ['M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16Z', 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z', 'M12 2v3M12 19v3M2 12h3M19 12h3'],
  expand: ['M14 4h6v6', 'M20 4 12 12', 'M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4'],
  arrow: ['M4 12h15', 'm14 7 5 5-5 5'],
  'chevron-r': ['m9 6 6 6-6 6'],
  clockfast: ['M12 7v5l3 2', 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z'],
}

const props = withDefaults(defineProps<{ name: string; size?: number; stroke?: number }>(), {
  size: 14,
  stroke: 1.8,
})
</script>

<template>
  <svg
    :width="props.size"
    :height="props.size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    :stroke-width="props.stroke"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    class="shrink-0"
  >
    <path v-for="(d, index) in PATHS[props.name] ?? PATHS.info" :key="index" :d="d" />
  </svg>
</template>

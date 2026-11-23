<script setup lang="ts">
/**
 * appearance: base-select 官方关闭态结构：<button><selectedcontent></selectedcontent></button>。
 * 抽成组件的原因：Vue 模板编译器按 HTML 规范对 <select> 的「直接子元素」做嵌套校验，
 * 会误报「<button> cannot be child of <select>」；组件节点不参与该校验，
 * 且组件模板内 button 是根节点、selectedcontent 不在非法嵌套表里，均不会触发告警。
 * 配套样式见 style.css 中 @supports (appearance: base-select) 段落：
 * button/selectedcontent 负责关闭态文本在箭头前单行省略。
 */
</script>

<template>
  <button><selectedcontent></selectedcontent></button>
</template>

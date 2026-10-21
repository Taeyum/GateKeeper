<script setup lang="ts">
import { ICON_PATHS, type IconName } from './icons'

// 장식이라 스크린 리더에는 숨기고, 뜻은 옆의 글자가 전한다.
withDefaults(defineProps<{ name: IconName; size?: number; strokeWidth?: number }>(), {
  size: 16,
  strokeWidth: 2.2,
})
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    :stroke-width="strokeWidth"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    :class="['icon', { spin: name === 'spinner' }]"
    v-html="ICON_PATHS[name]"
  />
</template>

<style scoped>
.icon {
  flex: none;
}
.spin {
  animation: gk-spin 1s linear infinite;
}
</style>

<script setup lang="ts">
// 디자인에서 쓰는 선 아이콘. 장식이라 스크린 리더에는 숨기고, 뜻은 옆의 글자가 전한다.
// 아래 문자열은 이 파일에 고정된 신뢰할 수 있는 SVG 조각이다(사용자 입력이 들어가지 않는다).
const PATHS = {
  shield: '<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3z"/><path d="M9 12l2 2 4-4"/>',
  'check-circle': '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.5 2.5L16 9.5"/>',
  'x-circle': '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
  alert: '<path d="M12 4l9 16H3L12 4z"/><path d="M12 10v4M12 17.2v.1"/>',
  bolt: '<path d="M13 3L5 13h6l-1 8 8-10h-6l1-8z"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  'arrow-right': '<path d="M5 12h14M13 6l6 6-6 6"/>',
  pulse: '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.1"/>',
  spinner: '<circle cx="12" cy="12" r="9" opacity=".25"/><path d="M21 12a9 9 0 00-9-9"/>',
  dashed: '<circle cx="12" cy="12" r="9" stroke-dasharray="3 3"/>',
  minus: '<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',
} as const

export type IconName = keyof typeof PATHS

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
    v-html="PATHS[name]"
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

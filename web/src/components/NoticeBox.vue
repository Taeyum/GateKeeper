<script setup lang="ts">
import AppIcon from './AppIcon.vue'

// 안내와 경고를 한 줄로 알리는 상자. 아이콘과 글자를 함께 쓰고 role로 스크린 리더에도 알린다.
withDefaults(defineProps<{ tone?: 'info' | 'warn' | 'error' }>(), { tone: 'info' })
</script>

<template>
  <div class="notice" :class="`tone-${tone}`" :role="tone === 'info' ? 'status' : 'alert'">
    <AppIcon :name="tone === 'info' ? 'info' : 'alert'" :size="18" />
    <div class="body"><slot /></div>
  </div>
</template>

<style scoped>
.notice {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 14px 18px;
  border: 1px solid;
  border-radius: var(--radius-sm);
  font-size: 14.5px;
}
.notice :deep(svg) {
  margin-top: 3px;
}
.body {
  min-width: 0;
}
.tone-info {
  background: var(--blue-bg);
  border-color: var(--blue-line);
  color: var(--ink);
}
.tone-info :deep(svg) {
  color: var(--blue);
}
.tone-warn {
  background: var(--warn-bg);
  border-color: var(--warn-line);
  color: var(--ink);
}
.tone-warn :deep(svg) {
  color: var(--warn-fg);
}
.tone-error {
  background: var(--reject-bg);
  border-color: var(--reject-line);
  color: var(--ink);
}
.tone-error :deep(svg) {
  color: var(--reject-fg);
}
</style>

<script setup lang="ts">
import type { Tone } from '@/lib/presentation'
import AppIcon from './AppIcon.vue'
import type { IconName } from './icons'

// 판정과 상태를 알리는 알약 배지. 색만으로 구분하지 않도록 글자와 아이콘을 늘 함께 보여 준다.
withDefaults(defineProps<{ tone: Tone; icon: IconName; label: string; size?: 'sm' | 'md' | 'lg' }>(), {
  size: 'md',
})
</script>

<template>
  <span class="badge" :class="[`tone-${tone}`, `size-${size}`]">
    <AppIcon :name="icon" :size="size === 'lg' ? 20 : size === 'sm' ? 13 : 15" />
    {{ label }}
  </span>
</template>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid;
  border-radius: var(--radius-pill);
  font-weight: 600;
  white-space: nowrap;
}
.size-sm {
  height: 24px;
  padding: 0 9px;
  font-size: 12.5px;
}
.size-md {
  height: 30px;
  padding: 0 12px;
  font-size: 14px;
}
.size-lg {
  height: 42px;
  gap: 8px;
  padding: 0 18px;
  font-size: 18px;
  font-weight: 700;
}
.tone-reject {
  background: var(--reject-bg);
  border-color: var(--reject-line);
  color: var(--reject-fg);
}
.tone-warn {
  background: var(--warn-bg);
  border-color: var(--warn-line);
  color: var(--warn-fg);
}
.tone-ok {
  background: var(--ok-bg);
  border-color: var(--ok-line);
  color: var(--ok-fg);
}
.tone-auto {
  background: var(--auto-bg);
  border-color: var(--auto-line);
  color: var(--auto-fg);
}
.tone-wait {
  background: var(--wait-bg);
  border-color: var(--wait-line);
  color: var(--wait-fg);
}
</style>

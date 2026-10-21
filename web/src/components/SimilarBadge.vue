<script setup lang="ts">
import AppIcon from './AppIcon.vue'

// "유사 장애 있음"을 알리는 어두운 알약. 결재함에서 눈에 띄게 하려고 판정 색과 다르게 검정으로 쓴다.
withDefaults(defineProps<{ label?: string; size?: 'sm' | 'md' }>(), { label: '비슷한 사고', size: 'sm' })
</script>

<template>
  <span class="similar" :class="`size-${size}`">
    <AppIcon name="pulse" :size="size === 'md' ? 15 : 12" :stroke-width="2.4" />
    {{ label }}
  </span>
</template>

<style scoped>
.similar {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border-radius: var(--radius-pill);
  background: var(--ink);
  color: #fff;
  font-weight: 600;
  white-space: nowrap;
  vertical-align: middle;
}
.size-sm {
  height: 24px;
  padding: 0 9px;
  font-size: 12.5px;
}
.size-md {
  height: 36px;
  padding: 0 14px;
  gap: 7px;
  font-size: 14.5px;
}
</style>

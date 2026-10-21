<script setup lang="ts">
import { computed } from 'vue'
import type { StepState, TimelineStep } from '@/lib/review'
import AppIcon from './AppIcon.vue'
import type { IconName } from './icons'

const props = defineProps<{ step: TimelineStep }>()

const ICONS: Record<StepState, IconName> = {
  waiting: 'dashed',
  running: 'spinner',
  done: 'check-circle',
  failed: 'alert',
  skipped: 'minus',
  notrun: 'minus',
}

// 승인자 결재 노드가 "확인 중"일 때는 사람을 기다리는 것이라 시계 아이콘을 쓴다.
const icon = computed<IconName>(() =>
  props.step.node === 'approval_gate' && props.step.state === 'running' ? 'clock' : ICONS[props.step.state],
)
</script>

<template>
  <div class="node" :class="`s-${step.state}`">
    <div class="row">
      <AppIcon :name="icon" :size="20" :stroke-width="2.2" />
      <span class="label">{{ step.label }}</span>
    </div>
    <div class="state">{{ step.text }}</div>
  </div>
</template>

<style scoped>
.node {
  flex: none;
  width: 168px;
  height: 72px;
  padding: 13px 14px;
  background: var(--surface);
  border: 1px solid var(--line2);
  border-radius: 14px;
}
.row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
}
.state {
  margin-top: 2px;
  padding-left: 28px;
  color: var(--ink2);
  font-size: 14px;
}
.s-running {
  background: #eef1ff;
  border-color: var(--blue);
}
.s-running .row {
  color: var(--ink);
}
.s-running :deep(svg),
.s-running .state {
  color: var(--blue);
}
.s-running .state {
  font-weight: 600;
}
.s-waiting,
.s-skipped,
.s-notrun {
  background: var(--soft);
  border-style: dashed;
}
.s-waiting .row,
.s-skipped .row,
.s-notrun .row {
  color: var(--ink2);
}
.s-waiting .state,
.s-skipped .state,
.s-notrun .state {
  color: var(--ink3);
}
.s-waiting :deep(svg),
.s-skipped :deep(svg),
.s-notrun :deep(svg) {
  color: #8a8ea8;
}
.s-failed {
  background: var(--warn-bg);
  border-color: var(--warn-line);
}
.s-failed :deep(svg),
.s-failed .state {
  color: var(--warn-fg);
}
.s-failed .state {
  font-weight: 600;
}
</style>

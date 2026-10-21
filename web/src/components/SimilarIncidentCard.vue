<script setup lang="ts">
import type { HistoryItem } from '@/api/types'
import { formatMonth } from '@/lib/format'
import AppIcon from './AppIcon.vue'

// 비슷한 사고: 이력 중 사고(outcome이 장애)였던 항목을 쉬운 말로 풀어 준다. 판정이 아니라 위험도에만 반영된다.
defineProps<{ incident: HistoryItem }>()
</script>

<template>
  <section class="card box" aria-labelledby="similar-title">
    <div class="head">
      <span class="icon"><AppIcon name="pulse" :size="18" :stroke-width="2.2" /></span>
      <h2 id="similar-title">비슷한 사고가 있었어요</h2>
    </div>
    <p class="title">{{ formatMonth(incident.date) }}, {{ incident.title }}</p>
    <dl v-if="incident.incident">
      <div class="row">
        <dt>심각도 / 지속 시간</dt>
        <dd>{{ incident.incident.severity }} / {{ incident.incident.duration_minutes }}분</dd>
      </div>
      <div class="row">
        <dt>원인</dt>
        <dd>{{ incident.incident.root_cause }}</dd>
      </div>
      <div class="row">
        <dt>교훈</dt>
        <dd>{{ incident.incident.lesson }}</dd>
      </div>
    </dl>
    <p class="foot">이 사고는 위험도에만 반영돼요. 승인/반려 판정에는 영향이 없어요.</p>
  </section>
</template>

<style scoped>
.box {
  padding: 28px;
}
.head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 10px;
  background: var(--ink);
  color: #fff;
}
h2 {
  font-size: 18px;
  font-weight: 600;
}
.title {
  margin-bottom: 14px;
  font-size: 16px;
  font-weight: 600;
  line-height: 1.5;
}
.row {
  padding: 10px 0;
  border-top: 1px solid var(--line);
}
dt {
  color: var(--ink3);
  font-size: 13.5px;
}
dd {
  color: var(--ink2);
}
.foot {
  margin-top: 6px;
  color: var(--ink3);
  font-size: 13.5px;
}
</style>

<script setup lang="ts">
import { computed } from 'vue'
import type { TimelineStep, TimelineNode as TimelineNodeName } from '@/lib/review'
import TimelineNode from './TimelineNode.vue'

// 검토 단계 8개를 GitHub Actions처럼 그린 그래프: 앞의 두 단계가 끝나면 규정 확인 / 영향 확인 / 사고 사례 확인
// 세 갈래가 동시에 돌고, 모두 끝나면 합쳐져 결과 정리로 이어진다. 선 색은 지나온 길만 진하게 칠한다.
const props = defineProps<{ steps: TimelineStep[] }>()

const by = computed(() => Object.fromEntries(props.steps.map((s) => [s.node, s])) as Record<TimelineNodeName, TimelineStep>)

/** 이 노드를 지나왔는가: 끝났거나(확인 못 한 것 포함) 건너뛴 노드 */
const passed = (node: TimelineNodeName) => ['done', 'failed', 'skipped'].includes(by.value[node].state)
const allBranchesPassed = computed(() => (['policy_agent', 'impact_agent', 'history_agent'] as const).every(passed))
</script>

<template>
  <div class="scroller">
    <div class="graph">
      <TimelineNode :step="by.parse_request" />
      <i class="link" :class="{ lit: passed('parse_request') }" aria-hidden="true" />
      <TimelineNode :step="by.classify" />

      <div class="fan" aria-hidden="true">
        <i class="h-in" :class="{ lit: passed('classify') }" />
        <i class="v" :class="{ lit: passed('classify') }" />
        <i class="s1" :class="{ lit: passed('classify') }" />
        <i class="s2" :class="{ lit: passed('classify') }" />
        <i class="s3" :class="{ lit: passed('classify') }" />
      </div>

      <div class="group">
        <TimelineNode :step="by.policy_agent" />
        <TimelineNode :step="by.impact_agent" />
        <TimelineNode :step="by.history_agent" />
      </div>

      <div class="fan" aria-hidden="true">
        <i class="h-out" :class="{ lit: allBranchesPassed }" />
        <i class="v" :class="{ lit: allBranchesPassed }" />
        <i class="s1 end" :class="{ lit: passed('policy_agent') }" />
        <i class="s2 end" :class="{ lit: passed('impact_agent') }" />
        <i class="s3 end" :class="{ lit: passed('history_agent') }" />
      </div>

      <TimelineNode :step="by.decide" />
      <i class="link" :class="{ lit: passed('decide') }" aria-hidden="true" />
      <TimelineNode :step="by.validate" />
      <i class="link" :class="{ lit: passed('validate') }" aria-hidden="true" />
      <TimelineNode :step="by.approval_gate" />
    </div>
  </div>
</template>

<style scoped>
.scroller {
  overflow-x: auto;
  padding-bottom: 6px;
}
.graph {
  display: flex;
  align-items: center;
  width: max-content;
}
.group {
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.link {
  flex: none;
  display: block;
  width: 24px;
  height: 2px;
  background: var(--line2);
}
.lit {
  background: var(--ink2) !important;
}

/* 갈래 연결선: 노드 높이 72, 간격 12라서 세 노드의 가운데는 위에서 36, 132, 228이다. */
.fan {
  flex: none;
  position: relative;
  width: 28px;
  height: 240px;
}
.fan i {
  position: absolute;
  display: block;
  background: var(--line2);
}
.fan .v {
  left: 13px;
  top: 35px;
  width: 2px;
  height: 194px;
}
.fan .h-in {
  left: 0;
  top: 131px;
  width: 14px;
  height: 2px;
}
.fan .h-out {
  left: 13px;
  top: 131px;
  width: 15px;
  height: 2px;
}
.fan .s1,
.fan .s2,
.fan .s3 {
  left: 13px;
  width: 15px;
  height: 2px;
}
.fan .s1.end,
.fan .s2.end,
.fan .s3.end {
  left: 0;
  width: 15px;
}
.fan .s1 {
  top: 35px;
}
.fan .s2 {
  top: 131px;
}
.fan .s3 {
  top: 227px;
}
</style>

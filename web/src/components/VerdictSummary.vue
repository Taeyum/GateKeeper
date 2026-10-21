<script setup lang="ts">
import { computed } from 'vue'
import type { Recommendation, RequestStatus } from '@/api/types'
import SimilarBadge from './SimilarBadge.vue'
import StatusBadge from './StatusBadge.vue'
import RiskMeter from './RiskMeter.vue'
import VerdictBadge from './VerdictBadge.vue'

// 결과 화면 맨 위: 시스템 권고를 한 줄 결론으로 말하고, 위험도 / 비슷한 사고 / 처리 상태를 칩으로 보여 준다.
const props = defineProps<{
  recommendation: Recommendation
  status: RequestStatus
  hasSimilarIncident: boolean
  /** 고쳐야 할 묶음의 수 */
  fixCount: number
  tierNote: string | null
}>()

const copy = computed(() => {
  switch (props.recommendation.verdict) {
    case '반려':
      return {
        headline: '이대로는 승인되기 어려워요',
        sub:
          props.fixCount > 0
            ? `${props.fixCount}가지만 고치면 다시 검토받을 수 있어요. 최종 결정은 변경관리자가 해요.`
            : '내용을 고쳐서 다시 요청해 주세요. 최종 결정은 변경관리자가 해요.',
      }
    case '조건부 승인':
      return {
        headline: '조건을 채우면 승인받을 수 있어요',
        sub: `${props.fixCount > 0 ? `아래 ${props.fixCount}가지를 보완해 주세요. ` : ''}최종 결정은 변경관리자가 해요.`,
      }
    case '승인':
      return { headline: '규정에 어긋나는 곳이 없어요', sub: '승인을 권고해요. 최종 결정은 변경관리자가 해요.' }
    case '자동 승인':
      return { headline: '자동으로 승인됐어요', sub: '사전에 승인된 표준 변경이라 승인자 확인 없이 처리돼요.' }
  }
})
</script>

<template>
  <section class="card summary" aria-label="검토 결과 요약">
    <VerdictBadge :verdict="recommendation.verdict" size="lg" />
    <h2>{{ copy.headline }}</h2>
    <p class="sub">{{ copy.sub }}</p>

    <div class="chips">
      <span v-if="recommendation.risk" class="chip">
        <span class="chip-label">위험도</span>
        <RiskMeter :risk="recommendation.risk" />
      </span>
      <SimilarBadge v-if="hasSimilarIncident" label="비슷한 사고 있음" size="md" />
      <StatusBadge :status="status" />
    </div>
    <p v-if="tierNote" class="note">{{ tierNote }}</p>
    <ul v-if="recommendation.warnings.length" class="warnings">
      <li v-for="warning in recommendation.warnings" :key="warning">{{ warning }}</li>
    </ul>
  </section>
</template>

<style scoped>
.summary {
  padding: 32px;
}
h2 {
  margin: 18px 0 8px;
  font-size: 30px;
  font-weight: 600;
  letter-spacing: -0.02em;
  line-height: 1.3;
}
.sub {
  max-width: 580px;
  color: var(--ink2);
  font-size: 17px;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-top: 24px;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  height: 36px;
  padding: 0 14px;
  border: 1px solid var(--line2);
  border-radius: var(--radius-pill);
  background: var(--surface);
  font-size: 14.5px;
}
.chip-label {
  color: var(--ink3);
}
.note {
  margin-top: 12px;
  color: var(--ink3);
  font-size: 14px;
}
.warnings {
  margin-top: 12px;
  color: var(--warn-fg);
  font-size: 14px;
  font-weight: 600;
}
</style>

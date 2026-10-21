<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ApiError, api } from '@/api/client'
import type { DecisionResponse, Tier } from '@/api/types'
import AppIcon from '@/components/AppIcon.vue'
import EvidenceDetails from '@/components/EvidenceDetails.vue'
import NoticeBox from '@/components/NoticeBox.vue'
import RiskMeter from '@/components/RiskMeter.vue'
import SimilarBadge from '@/components/SimilarBadge.vue'
import VerdictBadge from '@/components/VerdictBadge.vue'
import { EMERGENCY_NOTICE, groupViolations } from '@/copy/rules'
import { needsReason, reasonHint, type Decision } from '@/lib/decision'
import { formatDiffValue } from '@/lib/review'
import { formatMonth, formatSubmitted, formatWhen } from '@/lib/format'
import { requestTitle } from '@/lib/presentation'
import type { ReviewDetail } from '@/lib/review'

// 결재함의 상세 패널: 시스템 권고를 쉬운 말로 요약하고, 영향 범위와 비슷한 사고를 보여 준 뒤 결정을 받는다.
const props = defineProps<{ detail: ReviewDetail }>()
const emit = defineEmits<{ decided: [response: DecisionResponse] }>()

const TIER_LABEL: Record<Tier, string> = { 'Tier-1': '핵심 서비스', 'Tier-2': '중요도 보통', 'Tier-3': '중요도 낮음' }

const rec = computed(() => props.detail.recommendation)
const title = computed(() =>
  requestTitle(props.detail.structured?.target ?? null, props.detail.structured?.change_type ?? null, props.detail.structured?.standard_change_id ?? null),
)
const fixes = computed(() => (rec.value ? groupViolations(rec.value.violations, props.detail.alternative_diff) : { groups: [], extraDiff: [] }))
const incident = computed(() => props.detail.similar_incidents.find((h) => h.outcome === '장애' && h.incident) ?? null)
const reasonHeading = computed(() => {
  switch (rec.value?.verdict) {
    case '반려':
      return '반려를 권고하는 이유'
    case '조건부 승인':
      return '보완이 필요한 이유'
    default:
      return '걸린 규정'
  }
})
const riskNote = computed(() => {
  const tierOne = props.detail.impact?.tier === 'Tier-1'
  const similar = props.detail.has_similar_incident
  if (tierOne && similar) return '핵심 서비스이고 비슷한 사고도 있었어서 위험도가 높아요.'
  if (tierOne) return '핵심 서비스라서 위험도가 높아요.'
  if (similar) return '비슷한 사고가 있었던 변경이라서 위험도를 한 단계 올렸어요.'
  return null
})
const isEmergency = computed(() => Boolean(props.detail.structured?.is_emergency || rec.value?.post_report_required))

/** 대안은 항목별 비교가 있으면 "작업 시간: 11/3(화) 오후 2시 / ..."로, 없으면 시스템이 쓴 문장 그대로 보여 준다. */
const alternativeText = computed(() => {
  const diff = props.detail.alternative_diff
  if (diff && diff.length) return diff.map((d) => `${d.label}: ${formatDiffValue(d.field, d.suggested)}`).join(' / ')
  return rec.value?.conditional_alternative ?? null
})

// --- 결정 ---------------------------------------------------------------------
const comment = ref('')
const submitting = ref<Decision | null>(null)
const error = ref<string | null>(null)
const commentEl = ref<HTMLTextAreaElement | null>(null)

// 다른 요청을 고르면 쓰던 사유와 오류를 비운다.
watch(
  () => props.detail.request_id,
  () => {
    comment.value = ''
    error.value = null
    submitting.value = null
  },
)

const canDecide = computed(() => props.detail.status === 'awaiting_approval')

// v-model은 한글 조합 중에 값을 갱신하지 않으므로 input 이벤트로 바로 받고, 결정을 누르는 순간 칸의 현재 값을 다시 읽는다.
function onCommentInput(event: Event) {
  comment.value = (event.target as HTMLTextAreaElement).value
  if (error.value) error.value = null
}

async function decide(decision: Decision) {
  if (submitting.value) return
  error.value = null
  comment.value = commentEl.value?.value ?? comment.value
  if (needsReason(rec.value?.verdict ?? null, decision) && comment.value.trim() === '') {
    error.value = reasonHint(rec.value?.verdict ?? null, decision)
    commentEl.value?.focus()
    return
  }
  submitting.value = decision
  try {
    const response = await api.decide(props.detail.request_id, { decision, comment: comment.value.trim() })
    emit('decided', response)
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '결정을 보내지 못했어요. 잠시 뒤에 다시 시도해 주세요.'
  } finally {
    submitting.value = null
  }
}
</script>

<template>
  <aside class="card panel" aria-label="선택한 요청 상세">
    <div class="block head">
      <h2>{{ title }}</h2>
      <p class="meta">
        {{ detail.requester }} / {{ formatSubmitted(detail.submitted_at) }} 접수
        <template v-if="detail.structured?.start"> / 작업 {{ formatWhen(detail.structured.start) }}</template>
      </p>
      <div v-if="rec" class="badges">
        <VerdictBadge :verdict="rec.verdict" />
        <span class="chip"><span class="chip-label">위험도</span><RiskMeter :risk="rec.risk" /></span>
        <SimilarBadge v-if="detail.has_similar_incident" label="비슷한 사고 있음" size="sm" />
      </div>
    </div>

    <template v-if="rec">
      <div class="block">
        <h3>시스템 설명</h3>
        <p class="text">{{ rec.explanation || '규정 검사 결과를 아래에 정리했어요.' }}</p>
        <p v-if="riskNote" class="small">{{ riskNote }}</p>
        <ul v-if="rec.warnings.length" class="warn-list">
          <li v-for="warning in rec.warnings" :key="warning">{{ warning }}</li>
        </ul>
      </div>

      <div class="block">
        <h3>{{ reasonHeading }}</h3>
        <p v-if="!fixes.groups.length" class="text">규정에 어긋나는 곳이 없어요.</p>
        <ul v-else class="reasons">
          <li v-for="group in fixes.groups" :key="group.key">
            <AppIcon
              :name="group.items.some((i) => i.severity === 'forbidden') ? 'x-circle' : 'alert'"
              :size="20"
              :class="group.items.some((i) => i.severity === 'forbidden') ? 'reject' : 'warn'"
            />
            <div>
              <div class="strong">{{ group.problem }}</div>
              <p v-for="item in group.items" :key="item.rule_id" class="small ink2">{{ item.plain }}</p>
              <p class="codes">관련 규정 <span v-for="item in group.items" :key="item.rule_id" class="code">{{ item.rule_id }}</span></p>
            </div>
          </li>
        </ul>
        <NoticeBox v-if="isEmergency" tone="warn" class="gap">{{ EMERGENCY_NOTICE }}</NoticeBox>
      </div>

      <div class="block">
        <h3>영향 범위</h3>
        <dl v-if="detail.impact">
          <div class="row"><dt>서비스</dt><dd>{{ detail.impact.name }} ({{ TIER_LABEL[detail.impact.tier] }})</dd></div>
          <div class="row"><dt>담당 팀</dt><dd>{{ detail.impact.owner_team }}</dd></div>
          <div class="row">
            <dt>같이 영향받는 서비스</dt>
            <dd>
              <template v-if="detail.impact.dependents.length"><span v-for="id in detail.impact.dependents" :key="id" class="code">{{ id }}</span></template>
              <template v-else>없음</template>
            </dd>
          </div>
          <div class="row">
            <dt>같은 시간의 다른 작업</dt>
            <dd>
              <template v-if="detail.impact.same_time_changes.length">
                <span v-for="c in detail.impact.same_time_changes" :key="c.schedule_id" class="block-line">{{ c.title }} ({{ formatWhen(c.start) }})</span>
              </template>
              <template v-else>없음</template>
            </dd>
          </div>
        </dl>
        <p v-else-if="detail.route === 'standard'" class="text">표준 변경은 영향도를 따로 확인하지 않아요.</p>
        <NoticeBox v-else tone="warn">영향도를 확인하지 못했어요. 직접 확인해 주세요.</NoticeBox>
      </div>

      <div v-if="incident && incident.incident" class="block">
        <h3>비슷한 사고 <SimilarBadge label="있음" size="sm" /></h3>
        <p class="text">{{ formatMonth(incident.date) }}, {{ incident.title }} ({{ incident.incident.severity }} / {{ incident.incident.duration_minutes }}분)</p>
        <p class="small">원인: {{ incident.incident.root_cause }}</p>
        <p class="small">이 사고는 위험도에만 반영되고, 권고 판정에는 영향이 없어요.</p>
      </div>

      <div v-if="alternativeText" class="block">
        <h3>요청자에게 제안한 대안</h3>
        <p class="text">{{ alternativeText }}</p>
      </div>

      <div class="block">
        <details>
          <summary>요청 원문과 규정 근거 보기</summary>
          <blockquote>{{ detail.text }}</blockquote>
          <EvidenceDetails :recommendation="rec" label="걸린 규정의 엔진 근거 문장" />
          <p class="small">요청 번호 {{ detail.request_id }}</p>
        </details>
      </div>
    </template>
    <div v-else class="block"><NoticeBox>아직 검토 결과가 없어요.</NoticeBox></div>

    <!-- 결정 -->
    <div class="block decide">
      <template v-if="canDecide">
        <label for="decision-comment">결정 사유 남기기</label>
        <textarea
          id="decision-comment"
          ref="commentEl"
          :value="comment"
          rows="3"
          placeholder="요청자에게 전달되고 기록으로 남아요."
          :aria-invalid="error ? 'true' : undefined"
          :aria-describedby="error ? 'decision-error' : 'decision-help'"
          @input="onCommentInput"
        />
        <p id="decision-help" class="small">시스템 권고와 다르게 결정한다면 이유를 꼭 적어 주세요.</p>
        <NoticeBox v-if="error" id="decision-error" tone="error" class="gap">{{ error }}</NoticeBox>
        <div class="buttons">
          <button type="button" class="btn reject" :disabled="submitting !== null" @click="decide('reject')">
            <AppIcon name="x-circle" :size="18" /> {{ submitting === 'reject' ? '보내는 중이에요' : '반려하기' }}
          </button>
          <button type="button" class="btn approve" :disabled="submitting !== null" @click="decide('approve')">
            <AppIcon name="check-circle" :size="18" /> {{ submitting === 'approve' ? '보내는 중이에요' : '승인하기' }}
          </button>
        </div>
      </template>
      <template v-else>
        <h3>{{ detail.status === 'approved' ? '승인했어요' : detail.status === 'rejected' ? '반려했어요' : detail.status === 'auto_approved' ? '자동 승인된 요청이에요' : '결정할 수 없는 요청이에요' }}</h3>
        <p v-if="detail.approval" class="small">{{ detail.approval.approver }} / {{ formatSubmitted(detail.approval.at) }}</p>
        <p v-if="detail.approval?.comment" class="comment">{{ detail.approval.comment }}</p>
        <p v-else-if="detail.approval" class="small">남긴 코멘트가 없어요.</p>
        <p v-if="detail.status === 'approved' || detail.status === 'rejected'" class="small">요청자에게 결과가 전달돼요.</p>
      </template>
    </div>
  </aside>
</template>

<style scoped>
.panel {
  overflow: hidden;
}
.block {
  padding: 22px 28px;
  border-top: 1px solid var(--line);
}
.head {
  padding-top: 28px;
  padding-bottom: 22px;
  border-top: 0;
}
h2 {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.01em;
}
h3 {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 16px;
  font-weight: 600;
}
.meta {
  margin: 4px 0 16px;
  color: var(--ink3);
  font-size: 14px;
}
.badges {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 30px;
  padding: 0 12px;
  border: 1px solid var(--line2);
  border-radius: var(--radius-pill);
  font-size: 14px;
}
.chip-label {
  color: var(--ink3);
}
.text {
  font-size: 15.5px;
}
.small {
  margin-top: 6px;
  color: var(--ink3);
  font-size: 14px;
}
.small.ink2 {
  margin-top: 2px;
  color: var(--ink2);
  font-size: 14.5px;
}
.strong {
  font-weight: 600;
}
.warn-list {
  margin-top: 8px;
  color: var(--warn-fg);
  font-size: 14px;
  font-weight: 600;
}
.reasons {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.reasons li {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.reasons :deep(svg) {
  margin-top: 2px;
}
.reasons :deep(svg.reject) {
  color: var(--reject-fg);
}
.reasons :deep(svg.warn) {
  color: var(--warn-fg);
}
.codes {
  margin-top: 4px;
  color: var(--ink3);
  font-size: 13px;
}
.code {
  display: inline-block;
  margin: 2px 6px 2px 0;
  padding: 1px 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--bg);
  font-family: var(--mono);
  font-size: 12.5px;
}
.gap {
  margin-top: 12px;
}
.row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 9px 0;
  border-top: 1px solid var(--line);
}
dt {
  flex: none;
  color: var(--ink2);
}
dd {
  font-weight: 600;
  text-align: right;
}
.block-line {
  display: block;
}
blockquote {
  margin: 12px 0;
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--soft);
  color: var(--ink2);
  line-height: 1.7;
  white-space: pre-wrap;
}
summary {
  font-weight: 600;
}
.decide {
  padding: 24px 28px 28px;
  background: var(--soft);
}
label {
  display: block;
  margin-bottom: 8px;
  font-size: 16px;
  font-weight: 600;
}
textarea {
  display: block;
  width: 100%;
  min-height: 92px;
  padding: 14px;
  border: 1px solid var(--line2);
  border-radius: var(--radius-sm);
  background: #fff;
  line-height: 1.6;
  resize: vertical;
}
textarea::placeholder {
  color: var(--ink3);
  opacity: 1;
}
.decide .small {
  margin-bottom: 4px;
}
.buttons {
  display: flex;
  gap: 12px;
  margin-top: 14px;
}
.btn {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 52px;
  border: 0;
  border-radius: var(--radius-pill);
  color: #fff;
  font-size: 17px;
  font-weight: 600;
}
.btn.reject {
  background: var(--reject-fg);
}
.btn.approve {
  background: var(--ok-fg);
}
.btn:hover:not(:disabled) {
  filter: brightness(0.94);
}
.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.comment {
  margin-top: 10px;
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: #fff;
  white-space: pre-wrap;
}
</style>

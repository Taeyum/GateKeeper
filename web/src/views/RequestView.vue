<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { ApiError, api } from '@/api/client'
import AppIcon from '@/components/AppIcon.vue'
import EvidenceDetails from '@/components/EvidenceDetails.vue'
import FixList from '@/components/FixList.vue'
import NoticeBox from '@/components/NoticeBox.vue'
import PageBand from '@/components/PageBand.vue'
import ProcessSteps from '@/components/ProcessSteps.vue'
import ReviewTimeline from '@/components/ReviewTimeline.vue'
import SimilarIncidentCard from '@/components/SimilarIncidentCard.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import UnderstoodCard from '@/components/UnderstoodCard.vue'
import VerdictSummary from '@/components/VerdictSummary.vue'
import { EMERGENCY_NOTICE, groupViolations } from '@/copy/rules'
import { RequestFlow, type Connection, type EventSourceLike } from '@/lib/flow'
import { formatSubmitted } from '@/lib/format'
import { requestTitle, STANDARD_LABEL } from '@/lib/presentation'
import { buildRetryText, timelineSteps, type ReviewDetail } from '@/lib/review'

const props = defineProps<{ id: string }>()
const router = useRouter()

// --- 상태 따라가기 -----------------------------------------------------------
const detail = ref<ReviewDetail | null>(null)
const connection = ref<Connection>('idle')
const loadError = ref<string | null>(null)

const flow = new RequestFlow(
  {
    getRequest: (id) => api.getRequest(id),
    streamUrl: (id) => api.streamUrl(id),
    createEventSource: (url) => new EventSource(url) as unknown as EventSourceLike,
  },
  {
    onDetail: (next) => {
      detail.value = next
      loadError.value = null
    },
    onConnection: (state) => {
      connection.value = state
    },
    onError: (error) => {
      loadError.value = error instanceof ApiError ? error.message : '검토 상태를 읽지 못했어요. 잠시 뒤에 다시 시도해 주세요.'
    },
  },
)

function load() {
  detail.value = null
  loadError.value = null
  void flow.start(props.id)
}
watch(() => props.id, load, { immediate: true })
onBeforeUnmount(() => flow.stop())

// --- 보여 줄 것 계산 ---------------------------------------------------------------
const name = computed(() => {
  const d = detail.value
  return requestTitle(d?.structured?.target ?? null, d?.structured?.change_type ?? null, d?.structured?.standard_change_id ?? null)
})

/** 결과 화면은 검토를 정상으로 마친 뒤(승인 대기, 승인, 반려, 자동 승인)에만 보인다. */
const phase = computed<'loading' | 'progress' | 'result'>(() => {
  const d = detail.value
  if (!d) return 'loading'
  const finished = ['awaiting_approval', 'approved', 'rejected', 'auto_approved'].includes(d.status)
  return finished && d.recommendation ? 'result' : 'progress'
})

const bandTitle = computed(() => {
  if (phase.value === 'result') return '검토 결과'
  if (detail.value?.status === 'manual_review') return '수동 검토로 넘겼어요'
  return phase.value === 'loading' ? '불러오는 중이에요' : '검토하고 있어요'
})

const steps = computed(() => (detail.value ? timelineSteps(detail.value.nodes, detail.value.status) : []))
/** 서버가 재시작하는 등으로 노드 기록이 없으면 단계별 표시 없이 결과만 보여 준다. */
const showTimeline = computed(() => detail.value !== null && (detail.value.nodes.length > 0 || detail.value.status === 'reviewing'))

const fixes = computed(() => {
  const d = detail.value
  return d?.recommendation ? groupViolations(d.recommendation.violations, d.alternative_diff) : { groups: [], extraDiff: [] }
})
const fixHeading = computed(() => (detail.value?.recommendation?.verdict === '조건부 승인' ? '이렇게 보완해 주세요' : '이렇게 고쳐 주세요'))
const hasFixes = computed(() => fixes.value.groups.length > 0 || fixes.value.extraDiff.length > 0)

const incident = computed(() => detail.value?.similar_incidents.find((h) => h.outcome === '장애' && h.incident) ?? null)
const tierNote = computed(() => {
  const impact = detail.value?.impact
  if (!impact || impact.tier !== 'Tier-1') return null
  return `${impact.name.replace(/\s*\(.*\)$/, '')}는 핵심 서비스라서 위험도가 높게 표시돼요.`
})

const standardLabel = computed(() => {
  const id = detail.value?.structured?.standard_change_id
  return id ? STANDARD_LABEL[id] : null
})
const isEmergency = computed(() => Boolean(detail.value?.structured?.is_emergency || detail.value?.recommendation?.post_report_required))
const retryText = computed(() => (detail.value ? buildRetryText(detail.value) : null))
const canRetry = computed(() => detail.value?.status !== 'approved' && detail.value?.status !== 'auto_approved' && hasFixes.value)

// --- 다시 요청 -------------------------------------------------------------------
const retrying = ref(false)
const retryError = ref<string | null>(null)

async function retry() {
  if (!detail.value || !retryText.value || retrying.value) return
  retrying.value = true
  retryError.value = null
  try {
    const created = await api.createRequest({ text: retryText.value, parent_request_id: detail.value.request_id })
    await router.push({ name: 'request', params: { id: created.request_id } })
  } catch (error) {
    retryError.value = error instanceof ApiError ? error.message : '다시 요청하지 못했어요. 잠시 뒤에 다시 시도해 주세요.'
  } finally {
    retrying.value = false
  }
}
</script>

<template>
  <PageBand :title="bandTitle" active="mine">
    <template #breadcrumb>
      <RouterLink :to="{ path: '/', hash: '#my-requests' }" class="crumb-link">내 요청</RouterLink>
      <span aria-hidden="true"> / </span>
      <span class="crumb-now">{{ detail?.structured ? name : '새 요청' }}</span>
    </template>
    <template #actions>
      <span v-if="phase === 'progress' && detail?.status === 'reviewing'" class="live"><AppIcon name="spinner" :size="16" :stroke-width="2.4" /> 확인 중</span>
      <span v-else-if="detail">{{ formatSubmitted(detail.submitted_at) }} 접수</span>
    </template>
  </PageBand>

  <main class="page">
    <!-- 불러오기 -->
    <p v-if="phase === 'loading' && !loadError" class="loading" role="status">불러오는 중이에요.</p>
    <div v-if="loadError" class="load-error">
      <NoticeBox tone="error">{{ loadError }}</NoticeBox>
      <button type="button" class="link-button" @click="load">다시 불러오기</button>
    </div>

    <!-- 진행 -->
    <div v-if="phase === 'progress' && detail" class="col">
      <NoticeBox v-if="connection === 'recovering'">연결이 잠시 끊겨서 다시 연결하고 있어요. 검토는 계속되고 있어요.</NoticeBox>

      <NoticeBox v-if="detail.status === 'manual_review'" tone="warn">
        <strong>{{ detail.message ?? '자동 검토를 끝내지 못해 수동 검토로 넘겼어요.' }}</strong>
        <div class="sub-note">변경관리자가 직접 확인해요. 결과가 나오면 “내 요청”에서 확인할 수 있어요.</div>
      </NoticeBox>
      <NoticeBox v-else-if="detail.errors.length" tone="warn">
        일부는 확인하지 못했어요({{ detail.errors.join(', ') }}). 확인하지 못한 부분은 승인자가 직접 확인해요.
      </NoticeBox>

      <section v-if="showTimeline" class="card pipe" aria-labelledby="pipe-title">
        <h2 id="pipe-title">{{ detail.status === 'manual_review' ? '여기까지 확인했어요' : '지금 이런 순서로 확인하고 있어요' }}</h2>
        <p class="lead">
          {{
            detail.status === 'manual_review'
              ? '자동 검토는 여기서 멈췄고, 나머지는 사람이 이어서 확인해요.'
              : '규정, 서비스 영향, 과거 사고는 동시에 확인해서 빨라요. 창을 닫아도 확인은 계속돼요.'
          }}
        </p>
        <ReviewTimeline :steps="steps" />

        <div class="pipe-foot">
          <details>
            <summary>각 단계는 무엇을 하나요?</summary>
            <dl class="explain">
              <div><dt>요청 이해</dt><dd>쓰신 문장을 대상, 시간, 접속할 곳으로 나눠요.</dd></div>
              <div><dt>종류 확인</dt><dd>방화벽, 배포처럼 어떤 변경인지 구분해요.</dd></div>
              <div><dt>규정 확인</dt><dd>사내 변경 규정에 어긋나는 부분이 있는지 비교해요.</dd></div>
              <div><dt>영향 확인</dt><dd>바꾸려는 서비스를 쓰는 다른 서비스와 같은 시간의 다른 작업을 봐요.</dd></div>
              <div><dt>사고 사례 확인</dt><dd>비슷한 변경으로 사고가 난 적이 있는지 찾아요.</dd></div>
              <div><dt>결과 정리 / 내용 점검</dt><dd>결과와 설명을 만들고, 설명이 규정과 맞는지 한 번 더 확인해요.</dd></div>
            </dl>
          </details>
        </div>
      </section>

      <div class="two">
        <section class="card mine" aria-labelledby="mine-title">
          <h2 id="mine-title">내가 쓴 내용</h2>
          <blockquote>{{ detail.text }}</blockquote>
          <p class="who">{{ detail.requester }} / {{ formatSubmitted(detail.submitted_at) }} 접수</p>
        </section>
        <UnderstoodCard v-if="detail.structured" :structured="detail.structured" :service-name="detail.impact?.name" class="understood" />
        <section v-else class="card mine understood" aria-labelledby="wait-title">
          <h2 id="wait-title">이렇게 이해했어요</h2>
          <p class="lead">요청 내용을 항목으로 나누는 중이에요.</p>
        </section>
      </div>
    </div>

    <!-- 결과 -->
    <div v-if="phase === 'result' && detail && detail.recommendation" class="result">
      <div class="col main">
        <VerdictSummary
          :recommendation="detail.recommendation"
          :status="detail.status"
          :has-similar-incident="detail.has_similar_incident"
          :fix-count="fixes.groups.length"
          :tier-note="tierNote"
          :standard-label="standardLabel"
        />

        <section v-if="detail.approval" class="card decision" aria-labelledby="decision-title">
          <h2 id="decision-title">{{ detail.approval.decision === 'approve' ? '승인됐어요' : '반려됐어요' }}</h2>
          <p class="who">{{ detail.approval.approver }} / {{ formatSubmitted(detail.approval.at) }}</p>
          <p v-if="detail.approval.comment" class="comment">{{ detail.approval.comment }}</p>
          <p v-else class="who">남긴 코멘트가 없어요.</p>
        </section>

        <NoticeBox v-if="isEmergency" tone="warn">{{ EMERGENCY_NOTICE }}</NoticeBox>

        <section v-if="hasFixes" class="card fix" aria-labelledby="fix-heading">
          <FixList :heading="fixHeading" :groups="fixes.groups" :extra-diff="fixes.extraDiff" />

          <template v-if="canRetry">
            <div class="cta">
              <button v-if="retryText" type="button" class="primary" :disabled="retrying" @click="retry">
                {{ retrying ? '보내는 중이에요' : '이 내용으로 다시 요청하기' }}
                <AppIcon name="arrow-right" :size="18" />
              </button>
              <RouterLink :to="{ path: '/', query: { parent: detail.request_id } }" class="secondary">직접 고쳐 쓰기</RouterLink>
            </div>
            <NoticeBox v-if="retryError" tone="error" class="retry-error">{{ retryError }}</NoticeBox>
            <p v-if="retryText" class="cta-note">
              <AppIcon name="check-circle" :size="18" />
              같은 요청에 이어서 새 요청으로 검토해요. 처음부터 다시 쓰지 않아도 돼요.
            </p>
          </template>
        </section>

        <section v-if="detail.recommendation.violations.length || detail.recommendation.explanation" class="card evidence">
          <EvidenceDetails :recommendation="detail.recommendation" />
        </section>
      </div>

      <div class="col side">
        <SimilarIncidentCard v-if="incident" :incident="incident" />
        <ProcessSteps :status="detail.status" :nodes="detail.nodes" :approval="detail.approval" />
      </div>
    </div>
  </main>
</template>

<style scoped>
.crumb-link {
  text-decoration: underline;
  text-underline-offset: 3px;
}
.crumb-now {
  color: #fff;
}
.live {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 38px;
  padding: 0 18px;
  border: 1px solid rgba(255, 255, 255, 0.22);
  border-radius: var(--radius-pill);
  background: rgba(255, 255, 255, 0.12);
  font-weight: 600;
}
.loading {
  color: var(--ink2);
}
.load-error {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-width: 720px;
}
.link-button {
  align-self: flex-start;
  padding: 0;
  border: 0;
  background: none;
  color: var(--blue);
  font-weight: 600;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.col {
  display: flex;
  flex-direction: column;
  gap: 24px;
  min-width: 0;
}
.sub-note {
  margin-top: 2px;
  color: var(--ink2);
}
h2 {
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.01em;
}
.lead {
  margin: 4px 0 28px;
  color: var(--ink2);
}
.pipe {
  padding: 32px;
}
.pipe-foot {
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid var(--line);
}
summary {
  font-weight: 600;
}
.explain {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr));
  gap: 12px 32px;
  margin-top: 12px;
}
.explain dt {
  font-size: 14.5px;
  font-weight: 600;
}
.explain dd {
  color: var(--ink2);
  font-size: 14.5px;
}
.two {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
  align-items: flex-start;
}
.mine {
  flex: 1 1 420px;
  min-width: 0;
  padding: 32px;
}
.mine h2 {
  margin-bottom: 14px;
}
.understood {
  flex: 1.4 1 520px;
  min-width: 0;
}
.understood.mine .lead {
  margin: 0;
}
blockquote {
  padding: 18px 20px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--soft);
  font-size: 17px;
  line-height: 1.8;
  white-space: pre-wrap;
}
.who {
  margin-top: 14px;
  color: var(--ink3);
  font-size: 14px;
}
.result {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
  align-items: flex-start;
}
.main {
  flex: 999 1 640px;
}
.side {
  flex: 1 1 340px;
  max-width: 400px;
}
.fix {
  padding: 32px;
}
.cta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 28px;
}
.primary {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 52px;
  padding: 0 28px;
  border: 0;
  border-radius: var(--radius-pill);
  background: var(--blue);
  color: #fff;
  font-size: 17px;
  font-weight: 600;
}
.primary:hover:not(:disabled) {
  filter: brightness(0.94);
}
.primary:disabled {
  background: var(--line2);
  color: var(--ink3);
  cursor: not-allowed;
}
.secondary {
  display: inline-flex;
  align-items: center;
  height: 52px;
  padding: 0 24px;
  border: 1px solid var(--line2);
  border-radius: var(--radius-pill);
  background: #fff;
  font-size: 16px;
  font-weight: 600;
}
.secondary:hover {
  background: var(--soft);
}
.retry-error {
  margin-top: 12px;
}
.cta-note {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-top: 14px;
  color: var(--ink2);
  font-size: 14px;
}
.cta-note :deep(svg) {
  margin-top: 2px;
}
.evidence {
  padding: 24px 32px;
}
.decision {
  padding: 28px 32px;
}
.decision h2 {
  font-size: 22px;
}
.decision .who {
  margin-top: 2px;
}
.comment {
  margin-top: 12px;
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--soft);
  white-space: pre-wrap;
}
</style>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError, api } from '@/api/client'
import type { DecisionResponse, RequestSummary } from '@/api/types'
import AppIcon from '@/components/AppIcon.vue'
import ApprovalDetail from '@/components/ApprovalDetail.vue'
import NoticeBox from '@/components/NoticeBox.vue'
import PageBand from '@/components/PageBand.vue'
import RiskMeter from '@/components/RiskMeter.vue'
import RoleGate from '@/components/RoleGate.vue'
import SimilarBadge from '@/components/SimilarBadge.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import VerdictBadge from '@/components/VerdictBadge.vue'
import { formatSubmitted } from '@/lib/format'
import { requestTitle, STANDARD_LABEL } from '@/lib/presentation'
import type { ReviewDetail } from '@/lib/review'
import { session } from '@/state/session'

const route = useRoute()
const router = useRouter()

// 탭과 선택한 요청은 주소(?tab=auto&selected=...)에 담아 새로 고침해도 유지한다.
const tab = computed<'pending' | 'auto'>(() => (route.query.tab === 'auto' ? 'auto' : 'pending'))
const selectedId = computed(() => (typeof route.query.selected === 'string' ? route.query.selected : null))

function setTab(next: 'pending' | 'auto') {
  void router.replace({ query: next === 'auto' ? { tab: 'auto' } : {} })
}
function select(id: string) {
  void router.replace({ query: { ...route.query, selected: id } })
}

// --- 목록 ---------------------------------------------------------------------
const pending = ref<RequestSummary[] | null>(null)
const auto = ref<RequestSummary[] | null>(null)
const listError = ref<string | null>(null)

async function loadLists() {
  listError.value = null
  try {
    const [p, a] = await Promise.all([api.listApprovals('awaiting_approval'), api.listApprovals('auto_approved')])
    pending.value = p.items
    auto.value = a.items
  } catch (error) {
    listError.value = error instanceof ApiError ? error.message : '결재함을 불러오지 못했어요.'
  }
}

// 역할이 승인자일 때만 불러온다(요청자로 열면 서버가 403을 준다). 전환하면 다시 불러온다.
watch(
  () => session.role,
  (role) => {
    if (role === 'approver') void loadLists()
  },
  { immediate: true },
)

// 결재 대기 탭에서 고른 요청이 없으면 맨 위 요청(가장 위험한 요청)을 고른다.
watch(
  [pending, tab, selectedId],
  () => {
    if (tab.value === 'pending' && pending.value && pending.value.length > 0 && !selectedId.value) {
      select(pending.value[0].request_id)
    }
  },
  { immediate: true },
)

// --- 상세 ---------------------------------------------------------------------
const detail = ref<ReviewDetail | null>(null)
const detailError = ref<string | null>(null)
let detailRun = 0

async function loadDetail(id: string | null) {
  const run = ++detailRun
  detail.value = null
  detailError.value = null
  if (!id || session.role !== 'approver') return
  try {
    const loaded = await api.getRequest(id)
    if (run === detailRun) detail.value = loaded
  } catch (error) {
    if (run === detailRun) detailError.value = error instanceof ApiError ? error.message : '요청 내용을 불러오지 못했어요.'
  }
}
watch([selectedId, () => session.role], () => void loadDetail(selectedId.value), { immediate: true })

const decidedMessage = ref<string | null>(null)

async function onDecided(response: DecisionResponse) {
  if (detail.value) detail.value = { ...detail.value, status: response.status, approval: response.approval }
  decidedMessage.value = response.status === 'approved' ? '승인했어요. 요청자에게 결과가 전달돼요.' : '반려했어요. 요청자에게 결과가 전달돼요.'
  await loadLists()
}

async function nextRequest() {
  decidedMessage.value = null
  const next = pending.value?.find((item) => item.request_id !== selectedId.value)
  if (next) select(next.request_id)
  else await router.replace({ query: {} })
}

const items = computed(() => (tab.value === 'pending' ? pending.value : auto.value))
const counts = computed(() => ({ pending: pending.value?.length ?? 0, auto: auto.value?.length ?? 0 }))

function standardLabel(item: RequestSummary): string {
  return item.standard_change_id ? STANDARD_LABEL[item.standard_change_id] : '표준 변경'
}
</script>

<template>
  <PageBand
    title="결재함"
    subtitle="먼저 봐야 할 요청이 위에 있어요. 표준 변경은 규정에 어긋나지 않으면 자동 승인돼서 여기 올라오지 않아요."
  >
    <template #nav>
      <div class="tabs" role="tablist" aria-label="결재함 구분">
        <button type="button" role="tab" class="tab" :class="{ on: tab === 'pending' }" :aria-selected="tab === 'pending'" @click="setTab('pending')">
          결재 대기 <span class="count">{{ counts.pending }}</span>
        </button>
        <button type="button" role="tab" class="tab" :class="{ on: tab === 'auto' }" :aria-selected="tab === 'auto'" @click="setTab('auto')">
          자동 승인 기록 <span class="count">{{ counts.auto }}</span>
        </button>
      </div>
    </template>
  </PageBand>

  <main class="page">
    <RoleGate role="approver" what="결재함">
      <div v-if="listError" class="error">
        <NoticeBox tone="error">{{ listError }}</NoticeBox>
        <button type="button" class="link-button" @click="loadLists">다시 불러오기</button>
      </div>
      <p v-else-if="items === null" class="state" role="status">불러오는 중이에요.</p>

      <!-- 결재 대기 -->
      <div v-else-if="tab === 'pending'" class="layout">
        <section class="card list" aria-labelledby="list-title">
          <div class="list-head">
            <h2 id="list-title">결재 대기 {{ pending?.length ?? 0 }}건</h2>
            <span>위험도가 높은 순</span>
          </div>
          <p v-if="pending && pending.length === 0" class="empty">결재 대기 중인 요청이 없어요. 새 요청이 오면 여기에 보여요.</p>
          <div v-else class="scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">요청</th>
                  <th scope="col">시스템 권고</th>
                  <th scope="col">위험도</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in pending" :key="item.request_id" :class="{ sel: item.request_id === selectedId }" @click="select(item.request_id)">
                  <td>
                    <button type="button" class="pick" :aria-current="item.request_id === selectedId ? 'true' : undefined" @click.stop="select(item.request_id)">
                      <span class="t1">
                        {{ requestTitle(item.target, item.change_type, item.standard_change_id) }}
                        <SimilarBadge v-if="item.has_similar_incident" />
                        <span v-if="item.parent_request_id" class="again">다시 요청</span>
                      </span>
                      <span class="t2">{{ item.requester }} / {{ formatSubmitted(item.submitted_at) }}</span>
                    </button>
                  </td>
                  <td><VerdictBadge v-if="item.verdict" :verdict="item.verdict" /></td>
                  <td><RiskMeter :risk="item.risk" /></td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <div class="side">
          <NoticeBox v-if="decidedMessage" class="decided">
            {{ decidedMessage }}
            <button type="button" class="link-button" @click="nextRequest">다음 요청 보기</button>
          </NoticeBox>
          <NoticeBox v-if="detailError" tone="error">{{ detailError }}</NoticeBox>
          <ApprovalDetail v-if="detail" :detail="detail" @decided="onDecided" />
          <p v-else-if="!detailError && selectedId" class="state" role="status">요청 내용을 불러오는 중이에요.</p>
          <section v-else-if="!selectedId" class="card hint">
            <AppIcon name="info" :size="20" />
            왼쪽에서 요청을 고르면 자세한 내용이 여기에 보여요.
          </section>
        </div>
      </div>

      <!-- 자동 승인 기록 -->
      <div v-else class="layout">
        <section class="card list" aria-labelledby="auto-title">
          <div class="list-head">
            <div>
              <h2 id="auto-title">자동 승인 기록 {{ auto?.length ?? 0 }}건</h2>
              <p class="sub">사람이 보지 않고 규정 검사만으로 승인된 요청이에요.</p>
            </div>
          </div>
          <p v-if="auto && auto.length === 0" class="empty">아직 자동 승인된 요청이 없어요.</p>
          <div v-else class="scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">요청</th>
                  <th scope="col">종류</th>
                  <th scope="col">승인 시각</th>
                  <th scope="col">결과</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in auto" :key="item.request_id">
                  <td>
                    <span class="t1">{{ requestTitle(item.target, item.change_type, item.standard_change_id) }}</span>
                    <span class="t2">{{ item.requester }}</span>
                  </td>
                  <td>{{ standardLabel(item) }}</td>
                  <td class="nowrap">{{ item.decided_at ? formatSubmitted(item.decided_at) : '-' }}</td>
                  <td><StatusBadge status="auto_approved" /></td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <aside class="card rules" aria-labelledby="rules-title">
          <h2 id="rules-title">이런 경우에만 자동 승인돼요</h2>
          <ul>
            <li>
              <AppIcon name="bolt" :size="22" />
              <div><div class="strong">로그 레벨 변경</div><div class="ink2">재시작 없이 로그 레벨만 바꿀 때</div></div>
            </li>
            <li>
              <AppIcon name="bolt" :size="22" />
              <div><div class="strong">인증서 갱신</div><div class="ink2">같은 도메인 인증서를 만료 30일 이내에 교체할 때</div></div>
            </li>
          </ul>
          <p class="note top">정산 기간 같은 변경 금지 기간이나 다른 작업과 겹치면 자동 승인되지 않고 결재 대기로 올라와요.</p>
          <p class="note"><AppIcon name="info" :size="18" :stroke-width="2" /> 자동 승인도 모두 기록으로 남고, 주 1회 샘플로 다시 점검해요.</p>
        </aside>
      </div>
    </RoleGate>
  </main>
</template>

<style scoped>
.tabs {
  display: flex;
  gap: 6px;
}
.tab {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 40px;
  padding: 0 18px;
  border: 1px solid transparent;
  border-radius: var(--radius-pill);
  background: transparent;
  color: rgba(255, 255, 255, 0.82);
  font-size: 15px;
  font-weight: 500;
}
.tab.on {
  border-color: rgba(255, 255, 255, 0.22);
  background: rgba(255, 255, 255, 0.14);
  color: #fff;
  font-weight: 600;
}
.count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 22px;
  padding: 0 6px;
  border-radius: var(--radius-pill);
  background: rgba(255, 255, 255, 0.16);
  color: #fff;
  font-size: 13px;
}
.tab.on .count {
  background: #fff;
  color: var(--ink);
}
.layout {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
  align-items: flex-start;
}
.list {
  flex: 999 1 560px;
  min-width: 0;
  overflow: hidden;
}
/* 상세 패널이 길어도 목록이 화면에 남아 있게 한다(좁은 화면에서는 위아래로 쌓이므로 고정하지 않는다). */
@media (min-width: 1100px) {
  .layout > .list {
    position: sticky;
    top: 16px;
    align-self: flex-start;
    max-height: calc(100vh - 32px);
    overflow-y: auto;
  }
}
.list-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  padding: 24px 24px 16px;
}
.list-head h2 {
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.01em;
}
.list-head span,
.sub {
  color: var(--ink3);
  font-size: 14px;
}
.sub {
  margin-top: 2px;
}
.scroll {
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 15px;
}
th {
  padding: 12px 20px;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  background: var(--soft);
  color: var(--ink2);
  font-size: 13.5px;
  font-weight: 600;
  text-align: left;
  white-space: nowrap;
}
td {
  padding: 16px 20px;
  border-bottom: 1px solid var(--line);
  vertical-align: middle;
}
tr:last-child td {
  border-bottom: 0;
}
tbody tr {
  cursor: pointer;
}
tbody tr:hover td {
  background: var(--soft);
}
tr.sel td {
  background: var(--blue-bg);
}
.nowrap {
  white-space: nowrap;
}
.pick {
  display: block;
  width: 100%;
  padding: 0;
  border: 0;
  background: none;
  text-align: left;
}
.t1 {
  display: block;
  font-size: 16px;
  font-weight: 600;
  line-height: 1.5;
}
.t2 {
  display: block;
  margin-top: 2px;
  color: var(--ink3);
  font-size: 14px;
  font-weight: 400;
}
.again {
  margin-left: 6px;
  padding: 1px 8px;
  border: 1px solid var(--line2);
  border-radius: var(--radius-pill);
  color: var(--ink2);
  font-size: 12.5px;
  font-weight: 500;
  vertical-align: middle;
}
.empty {
  padding: 8px 24px 28px;
  color: var(--ink2);
}
.side {
  flex: 1 1 460px;
  max-width: 520px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.hint {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 24px 28px;
  color: var(--ink2);
}
.state {
  color: var(--ink2);
}
.error {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-width: 720px;
}
.link-button {
  margin-left: 6px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--blue);
  font-weight: 600;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.rules {
  flex: 1 1 420px;
  max-width: 480px;
  min-width: 0;
  padding: 28px;
}
.rules h2 {
  margin-bottom: 14px;
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.01em;
}
.rules ul {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.rules li {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.rules li :deep(svg) {
  margin-top: 2px;
  color: var(--auto-fg);
}
.strong {
  font-weight: 600;
}
.ink2 {
  color: var(--ink2);
  font-size: 14.5px;
}
.note {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-top: 10px;
  color: var(--ink3);
  font-size: 14px;
}
.note.top {
  margin-top: 18px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
  color: var(--ink2);
  font-size: 14.5px;
}
.note :deep(svg) {
  margin-top: 2px;
}
</style>

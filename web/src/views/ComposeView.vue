<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { ApiError, api } from '@/api/client'
import type { RequestSummary } from '@/api/types'
import AppIcon from '@/components/AppIcon.vue'
import NoticeBox from '@/components/NoticeBox.vue'
import PageBand from '@/components/PageBand.vue'
import RoleGate from '@/components/RoleGate.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { formatSubmitted, formatWhen } from '@/lib/format'
import { requestTitle } from '@/lib/presentation'
import { currentUser } from '@/state/session'

const route = useRoute()
const router = useRouter()

// --- 작성 ------------------------------------------------------------------
const text = ref('')
const submitting = ref(false)
const submitError = ref<string | null>(null)

/** 고쳐 쓰기: /?parent=요청번호 로 들어오면 이전 요청의 본문을 채우고, 새 요청에 parent_request_id를 붙인다. */
const parentId = computed(() => (typeof route.query.parent === 'string' ? route.query.parent : null))
const parentText = ref<string | null>(null)

onMounted(async () => {
  void loadMine()
  if (parentId.value) {
    try {
      const parent = await api.getRequest(parentId.value)
      parentText.value = parent.text
      if (!text.value) text.value = parent.text
    } catch {
      parentText.value = null
    }
  }
})

const canSubmit = computed(() => text.value.trim().length > 0 && !submitting.value)

async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  submitError.value = null
  try {
    const created = await api.createRequest({
      text: text.value.trim(),
      ...(parentId.value ? { parent_request_id: parentId.value } : {}),
    })
    await router.push({ name: 'request', params: { id: created.request_id } })
  } catch (error) {
    submitError.value = error instanceof ApiError ? error.message : '요청을 보내지 못했어요. 다시 시도해 주세요.'
  } finally {
    submitting.value = false
  }
}

// --- 유형별 안내 ---------------------------------------------------------------
type GuideKey = 'firewall' | 'deploy' | 'db' | 'standard'
const GUIDE_TABS: Array<{ key: GuideKey; label: string }> = [
  { key: 'firewall', label: '방화벽 열기' },
  { key: 'deploy', label: '앱 배포' },
  { key: 'db', label: 'DB 변경' },
  { key: 'standard', label: '자동 승인 대상' },
]
const GUIDES: Record<GuideKey, string[]> = {
  firewall: ['접속할 곳(IP)', '어떤 서비스의 어느 포트인지', '열어 둘 기간', '왜 필요한지'],
  deploy: ['작업 날짜와 시간', '문제가 생기면 되돌리는 방법', '테스트 서버에서 확인한 결과'],
  db: ['되돌리는 방법(백업 포함)', '테스트 결과', '핵심 서비스라면 하루 전 공지'],
  standard: ['재시작 없는 로그 레벨 변경', '만료 임박 인증서 갱신'],
}
const guide = ref<GuideKey>('firewall')

// --- 내 요청 -----------------------------------------------------------------
const mine = ref<RequestSummary[] | null>(null)
const mineError = ref<string | null>(null)

async function loadMine() {
  mineError.value = null
  try {
    mine.value = (await api.listRequests()).items
  } catch (error) {
    mine.value = null
    mineError.value = error instanceof ApiError ? error.message : '내 요청을 불러오지 못했어요.'
  }
}

function workTime(item: RequestSummary): string {
  return item.start ? `작업 ${formatWhen(item.start)}` : `접수 ${formatSubmitted(item.submitted_at)}`
}
</script>

<template>
  <PageBand
    title="변경 요청 작성"
    subtitle="하려는 일을 편하게 적어 주세요. 사내 규정에 맞는지 바로 확인해 드려요."
    active="compose"
  />

  <main class="page">
    <RoleGate role="requester" what="요청 작성">
      <div class="layout">
        <section class="card write" aria-labelledby="write-title">
          <h2 id="write-title">어떤 변경이 필요한가요?</h2>
          <p class="lead">무엇을, 언제, 어디서 접속해야 하는지 적어 주세요.</p>

          <NoticeBox v-if="parentId" class="parent">
            이전 요청을 고쳐서 다시 요청하는 중이에요. 이전에 쓴 내용을 불러왔으니 바꿀 부분만 고쳐 주세요.
          </NoticeBox>

          <form @submit.prevent="submit">
            <label for="req-text" class="sr-only">변경 내용</label>
            <textarea
              id="req-text"
              v-model="text"
              rows="6"
              placeholder="예: 다음 주 화요일 오후 2시에 payments-db 5432를 B페이먼츠 IP에서만 열어주세요"
              :aria-invalid="submitError ? 'true' : undefined"
              :aria-describedby="submitError ? 'submit-error' : 'emergency-hint'"
            />
            <p id="emergency-hint" class="hint">긴급한 변경이면 열려 있는 장애 티켓 번호(예: INC-2026-0412)도 같이 적어 주세요.</p>
            <NoticeBox v-if="submitError" id="submit-error" tone="error" class="error">{{ submitError }}</NoticeBox>

            <hr />

            <h3>이런 내용이 들어 있으면 한 번에 통과돼요</h3>
            <p class="guide-lead">빠진 내용은 검토 결과에서 “빠졌어요”로 알려 드려요.</p>
            <fieldset class="tabs">
              <legend class="sr-only">변경 유형별 안내</legend>
              <label v-for="tab in GUIDE_TABS" :key="tab.key" class="tab" :class="{ on: guide === tab.key }">
                <input v-model="guide" type="radio" name="guide" :value="tab.key" class="sr-only" />
                {{ tab.label }}
              </label>
            </fieldset>
            <p v-if="guide === 'standard'" class="guide-note">아래 두 가지는 규정에 어긋나지 않으면 승인자 없이 바로 승인돼요.</p>
            <ul class="checks">
              <li v-for="item in GUIDES[guide]" :key="item">
                <AppIcon :name="guide === 'standard' ? 'bolt' : 'check-circle'" :size="20" :class="guide === 'standard' ? 'auto' : 'blue'" />
                {{ item }}
              </li>
            </ul>
            <p v-if="guide === 'standard'" class="guide-note">적을 내용은 대상 서비스와 작업 시간이면 충분해요.</p>

            <div class="footer">
              <p class="mask"><AppIcon name="lock" :size="18" :stroke-width="2" /> 비밀번호나 키가 섞여 있으면 자동으로 가려져요.</p>
              <button type="submit" class="submit" :disabled="!canSubmit">
                {{ submitting ? '보내는 중이에요' : parentId ? '고쳐서 다시 요청하기' : '검토 요청하기' }}
                <AppIcon name="arrow-right" :size="18" />
              </button>
            </div>
          </form>
        </section>

        <aside id="my-requests" class="mine" aria-labelledby="my-title">
          <div class="card">
            <div class="mine-head">
              <h2 id="my-title">내 요청</h2>
              <p>상태가 바뀌면 여기에서 확인할 수 있어요.</p>
            </div>

            <p v-if="mine === null && !mineError" class="state">불러오는 중이에요.</p>
            <div v-else-if="mineError" class="state">
              <NoticeBox tone="error">{{ mineError }}</NoticeBox>
              <button type="button" class="reload" @click="loadMine">다시 불러오기</button>
            </div>
            <p v-else-if="mine && mine.length === 0" class="state">아직 보낸 요청이 없어요. 왼쪽에서 첫 요청을 작성해 보세요.</p>
            <ul v-else-if="mine">
              <li v-for="item in mine" :key="item.request_id">
                <RouterLink :to="{ name: 'request', params: { id: item.request_id } }" class="item">
                  <div class="item-title">{{ requestTitle(item.target, item.change_type, item.standard_change_id) }}</div>
                  <div class="item-meta">
                    {{ workTime(item) }}
                    <span v-if="item.parent_request_id" class="again">다시 요청</span>
                  </div>
                  <StatusBadge :status="item.status" />
                  <div v-if="item.verdict && item.status === 'awaiting_approval'" class="item-hint">시스템 권고: {{ item.verdict === '반려' ? '반려 권고' : item.verdict }}</div>
                </RouterLink>
              </li>
            </ul>
          </div>
          <p class="who">{{ currentUser.label }}의 요청만 보여요.</p>
        </aside>
      </div>
    </RoleGate>
  </main>
</template>

<style scoped>
.layout {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
  align-items: flex-start;
}
.write {
  flex: 999 1 600px;
  min-width: 0;
  padding: 32px;
}
h2 {
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.01em;
}
.lead {
  margin: 4px 0 16px;
  color: var(--ink2);
}
.parent {
  margin-bottom: 14px;
}
textarea {
  display: block;
  width: 100%;
  min-height: 184px;
  padding: 18px;
  border: 1px solid var(--line2);
  border-radius: 14px;
  background: #fff;
  font-size: 17px;
  line-height: 1.7;
  resize: vertical;
}
textarea::placeholder {
  color: var(--ink3);
  opacity: 1;
}
.hint {
  margin-top: 12px;
  color: var(--ink3);
  font-size: 14px;
}
.error {
  margin-top: 12px;
}
hr {
  margin: 28px 0;
  border: 0;
  border-top: 1px solid var(--line);
}
h3 {
  font-size: 17px;
  font-weight: 600;
}
.guide-lead {
  margin: 4px 0 14px;
  color: var(--ink2);
  font-size: 14px;
}
.tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0 0 14px;
  padding: 0;
  border: 0;
}
.tab {
  display: inline-flex;
  align-items: center;
  height: 40px;
  padding: 0 18px;
  border: 1px solid var(--line2);
  border-radius: var(--radius-pill);
  background: #fff;
  color: var(--ink2);
  font-size: 15px;
  font-weight: 500;
  cursor: pointer;
}
.tab.on {
  border-color: var(--ink);
  background: var(--ink);
  color: #fff;
  font-weight: 600;
}
.tab:has(input:focus-visible) {
  outline: 2px solid var(--blue);
  outline-offset: 2px;
}
.guide-note {
  margin: 0 0 10px;
  color: var(--ink2);
}
.checks {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(240px, 100%), 1fr));
  gap: 10px 24px;
}
.checks li {
  display: flex;
  align-items: center;
  gap: 10px;
}
.checks .blue {
  color: var(--blue);
}
.checks .auto {
  color: var(--auto-fg);
}
.guide-note:last-of-type {
  margin-top: 10px;
  color: var(--ink3);
  font-size: 14px;
}
.footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 28px;
  padding-top: 24px;
  border-top: 1px solid var(--line);
}
.mask {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--ink3);
  font-size: 14px;
}
.submit {
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
.submit:hover:not(:disabled) {
  filter: brightness(0.94);
}
.submit:disabled {
  background: var(--line2);
  color: var(--ink3);
  cursor: not-allowed;
}
.mine {
  flex: 1 1 380px;
  max-width: 440px;
  min-width: 0;
}
.mine .card {
  overflow: hidden;
}
.mine-head {
  padding: 22px 24px 14px;
}
.mine-head p {
  margin-top: 2px;
  color: var(--ink3);
  font-size: 14px;
}
.state {
  padding: 8px 24px 24px;
  color: var(--ink2);
}
.reload {
  margin-top: 10px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--blue);
  font-weight: 600;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.item {
  display: block;
  padding: 16px 24px;
  border-top: 1px solid var(--line);
}
.item:hover {
  background: var(--soft);
}
.item-title {
  font-size: 16px;
  font-weight: 600;
}
.item-meta {
  margin: 2px 0 10px;
  color: var(--ink3);
  font-size: 14px;
}
.again {
  margin-left: 8px;
  padding: 1px 8px;
  border: 1px solid var(--line2);
  border-radius: var(--radius-pill);
  color: var(--ink2);
  font-size: 12.5px;
}
.item-hint {
  margin-top: 8px;
  color: var(--ink3);
  font-size: 13.5px;
}
.who {
  margin-top: 10px;
  color: var(--ink3);
  font-size: 13.5px;
  text-align: right;
}
</style>

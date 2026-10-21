import type {
  AlternativeDiffItem,
  ChangeRequest,
  CreateRequestResponse,
  NodeName,
  NodeRun,
  RequestDetail,
  RequestStatus,
  SseEventMap,
  SseEventName,
} from '@/api/types'
import { formatRange, formatWhen } from './format'

// 검토 진행 상태를 다루는 순수 함수 모음이다. SSE 이벤트를 상세 응답(RequestDetail)에 접고,
// 타임라인의 노드 상태와 "이렇게 이해했어요" 행을 계산한다. 규칙은 docs/API_CONTRACT.md를 따른다.

/** 상세 응답에 화면이 이벤트에서만 얻는 값(수동 검토 안내 문장)을 더한 것 */
export type ReviewDetail = RequestDetail & { message?: string | null }

export function initialDetail(
  created: CreateRequestResponse,
  init: { text: string; requester: string; submitted_at: string },
): ReviewDetail {
  return {
    request_id: created.request_id,
    parent_request_id: created.parent_request_id,
    status: created.status,
    version: 'v2',
    requester: init.requester,
    submitted_at: init.submitted_at,
    text: init.text,
    route: null,
    nodes: [],
    structured: null,
    recommendation: null,
    alternative_diff: null,
    impact: null,
    similar_incidents: [],
    has_similar_incident: false,
    approval: null,
    errors: [],
    message: null,
  }
}

/** 이벤트 하나를 상세에 접어 새 상세를 돌려준다(원본은 바꾸지 않는다). */
export function applyEvent<K extends SseEventName>(detail: ReviewDetail, event: K, data: SseEventMap[K]): ReviewDetail {
  switch (event) {
    case 'node_started': {
      const e = data as SseEventMap['node_started']
      if (detail.nodes.some((n) => n.node === e.node)) return detail
      const run: NodeRun = { node: e.node, status: 'running', started_at: e.at, ended_at: null, error: null }
      return { ...detail, nodes: [...detail.nodes, run] }
    }
    case 'node_completed': {
      const e = data as SseEventMap['node_completed']
      const nodes = detail.nodes.map((n) =>
        n.node === e.node ? { ...n, status: e.error ? ('failed' as const) : ('done' as const), ended_at: e.at, error: e.error } : n,
      )
      return {
        ...detail,
        nodes,
        structured: e.structured ?? detail.structured,
        route: e.route ?? detail.route,
        errors: e.error ? [...detail.errors, e.error] : detail.errors,
      }
    }
    case 'recommendation': {
      const e = data as SseEventMap['recommendation']
      return {
        ...detail,
        recommendation: e.recommendation,
        alternative_diff: e.alternative_diff ?? null,
        impact: e.impact,
        similar_incidents: e.similar_incidents,
        has_similar_incident: e.has_similar_incident,
      }
    }
    case 'awaiting_approval':
      return { ...detail, status: 'awaiting_approval' }
    case 'done': {
      const e = data as SseEventMap['done']
      return { ...detail, status: e.status, approval: e.approval, message: e.message }
    }
    case 'error': {
      const e = data as SseEventMap['error']
      // 예상 못 한 서버 오류도 요청은 수동 검토로 넘어간다(계약 L3).
      return { ...detail, status: 'manual_review', message: e.message }
    }
    default:
      return detail
  }
}

// ---------------------------------------------------------------------------
// 타임라인
// ---------------------------------------------------------------------------

/** 화면에 그리는 노드 8개(finalize는 그리지 않는다). 순서가 곧 화면 순서다. */
export const TIMELINE_NODES = [
  'parse_request',
  'classify',
  'policy_agent',
  'impact_agent',
  'history_agent',
  'decide',
  'validate',
  'approval_gate',
] as const satisfies readonly NodeName[]
export type TimelineNode = (typeof TIMELINE_NODES)[number]

export const NODE_LABELS: Record<TimelineNode, string> = {
  parse_request: '요청 이해',
  classify: '종류 확인',
  policy_agent: '규정 확인',
  impact_agent: '영향 확인',
  history_agent: '사고 사례 확인',
  decide: '결과 정리',
  validate: '내용 점검',
  approval_gate: '승인자 결재',
}

export type StepState = 'waiting' | 'running' | 'done' | 'failed' | 'skipped' | 'notrun'

export interface TimelineStep {
  node: TimelineNode
  label: string
  state: StepState
  /** 상태 옆에 보여 줄 글자 */
  text: string
}

const NORMAL_END: RequestStatus[] = ['awaiting_approval', 'approved', 'rejected', 'auto_approved']

function stepText(node: TimelineNode, state: StepState): string {
  switch (state) {
    case 'waiting':
      return '대기'
    case 'running':
      return node === 'approval_gate' ? '결재 대기 중' : '확인 중'
    case 'done':
      return '완료'
    case 'failed':
      return '확인 못 했어요'
    case 'skipped':
      return '건너뜀'
    case 'notrun':
      return '진행 안 됨'
  }
}

/**
 * 계약의 "건너뜀 규칙": 서버는 실행한 노드만 보내므로, 시작 기록이 없는 노드는 요청이 어떻게 끝났느냐로 가른다.
 * - 아직 검토 중: 대기
 * - 정상으로 마침(승인 대기, 승인, 반려, 자동 승인): 건너뜀 (승인자 결재 노드는 건너뜀 대상이 아니라 대기)
 * - 검토를 끝내지 못함(수동 검토): 진행 안 됨
 */
export function timelineSteps(nodes: NodeRun[], status: RequestStatus): TimelineStep[] {
  return TIMELINE_NODES.map((node) => {
    const run = nodes.find((n) => n.node === node)
    let state: StepState
    if (run) {
      state = run.status === 'running' ? 'running' : run.status === 'failed' ? 'failed' : 'done'
    } else if (status === 'manual_review') {
      state = 'notrun'
    } else if (NORMAL_END.includes(status) && node !== 'approval_gate') {
      state = 'skipped'
    } else {
      state = 'waiting'
    }
    return { node, label: NODE_LABELS[node], state, text: stepText(node, state) }
  })
}

// ---------------------------------------------------------------------------
// "이렇게 이해했어요"
// ---------------------------------------------------------------------------

export interface UnderstoodRow {
  label: string
  value: string
  /** 값 밑에 붙이는 작은 설명 */
  note?: string
  /** 꼭 필요한 항목인데 빠졌다 */
  missing?: boolean
}

const NOT_WRITTEN = '적지 않았어요'

/** 사내망(10.0.0.0/8) 안의 출발지면 허용 기한이 필수가 아니다. 간단한 판단이고 정확한 판정은 규칙 엔진이 한다. */
function isInternalSource(source: string): boolean {
  return source.startsWith('10.')
}

function expiryText(c: ChangeRequest): string | null {
  if (c.expires_days !== null) return `${c.expires_days}일`
  if (c.expires_at !== null) return `${formatWhen(c.expires_at)}까지`
  return null
}

const WORK_LABEL: Record<ChangeRequest['change_type'], string> = {
  firewall: '외부 접속 허용(방화벽 열기)',
  deploy: '새 버전 배포',
  db_change: 'DB 변경',
  standard: '표준 변경',
}

/** 구조화 결과(structured)를 변경 유형별 필수 항목에 맞춰 보여 줄 행으로 만든다. 빠진 필수 항목에는 missing을 켠다. */
export function understoodRows(structured: ChangeRequest, serviceName?: string | null): UnderstoodRow[] {
  const rows: UnderstoodRow[] = [
    { label: '서비스', value: serviceName ? `${structured.target} (${serviceName})` : structured.target },
    { label: '작업', value: WORK_LABEL[structured.change_type] },
  ]

  if (structured.start) rows.push({ label: '시간', value: formatRange(structured.start, structured.end) })
  else rows.push({ label: '시간', value: NOT_WRITTEN, missing: true })

  switch (structured.change_type) {
    case 'firewall': {
      rows.push(
        structured.source
          ? { label: '접속할 곳(IP)', value: structured.source }
          : { label: '접속할 곳(IP)', value: NOT_WRITTEN, missing: true },
        structured.port !== null
          ? { label: '접속 포트', value: String(structured.port) }
          : { label: '접속 포트', value: '서비스 기본값', note: '적지 않아서 서비스의 기본 포트로 봤어요.' },
      )
      const expiry = expiryText(structured)
      const needsExpiry = !structured.source || !isInternalSource(structured.source)
      rows.push(
        expiry
          ? { label: '열어 둘 기간', value: expiry }
          : { label: '열어 둘 기간', value: NOT_WRITTEN, missing: needsExpiry },
      )
      break
    }
    case 'deploy':
    case 'db_change':
      rows.push(
        structured.has_rollback
          ? { label: '되돌리는 방법', value: '적혀 있어요' }
          : { label: '되돌리는 방법', value: NOT_WRITTEN, missing: true },
        structured.has_test_evidence
          ? { label: '테스트 결과', value: '붙어 있어요' }
          : { label: '테스트 결과', value: NOT_WRITTEN, missing: true },
      )
      break
    case 'standard':
      break
  }

  if (structured.reason) rows.push({ label: '이유', value: structured.reason })
  return rows
}

// ---------------------------------------------------------------------------
// 다시 요청
// ---------------------------------------------------------------------------

/** 값이 없을 때 필드마다 사람이 읽는 뜻이 다르다. 정하지 않은 접속할 곳은 "누구나 접속 가능"으로 본다(규정 CHG-02). */
const EMPTY_TEXT: Record<string, string> = {
  source: '정하지 않음 (누구나 접속 가능)',
  expires_days: '기간 없음',
  expires_at: '기간 없음',
}

function diffValueText(field: string, value: AlternativeDiffItem['suggested']): string {
  if (value === null) return EMPTY_TEXT[field] ?? '없음'
  if (typeof value === 'boolean') return value ? '있음' : '없음'
  if (typeof value === 'number') return field === 'expires_days' ? `${value}일` : String(value)
  if (field === 'start' || field === 'end' || field === 'expires_at') return formatWhen(value)
  return value
}

/** 대안 값을 사람이 읽는 문구로 바꾼다("지금 -> 이렇게" 카드에서도 쓴다). */
export function formatDiffValue(field: string, value: AlternativeDiffItem['current']): string {
  return diffValueText(field, value)
}

/**
 * 결과 화면의 "이 내용으로 다시 요청하기"가 보낼 본문. 서버가 부모 요청의 본문과 합쳐서 검토하므로
 * 여기에는 고칠 내용만 적는다. 항목별 대안이 있으면 그 값을, 없으면 대안 문장을 쓴다.
 */
export function buildRetryText(detail: Pick<RequestDetail, 'alternative_diff' | 'recommendation'>): string | null {
  const diff = detail.alternative_diff
  if (diff && diff.length > 0) {
    const parts = diff.map((item) => `${item.label}: ${diffValueText(item.field, item.suggested)}`)
    return `고친 내용으로 다시 요청합니다. ${parts.join(' / ')}`
  }
  const sentence = detail.recommendation?.conditional_alternative
  return sentence ? `고친 내용으로 다시 요청합니다. ${sentence}` : null
}

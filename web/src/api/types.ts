// docs/API_CONTRACT.md와 1:1로 맞춘 타입이다. 계약을 바꾸면 이 파일도 같이 바꾼다.
// 필드 이름은 app/schemas.py와 데이터 파일(cmdb.json, change_calendar.json, history.json)의 원래 키를 따른다.

// ---------------------------------------------------------------------------
// 공통 값
// ---------------------------------------------------------------------------

export type Role = 'requester' | 'approver'
export type ChangeType = 'firewall' | 'deploy' | 'db_change' | 'standard'
export type Severity = 'forbidden' | 'fixable'
export type Verdict = '반려' | '조건부 승인' | '승인' | '자동 승인'
export type Risk = '높음' | '중간' | '낮음'
export type Route = 'standard' | 'general' | 'emergency'
export type TimePrecision = 'exact' | 'range'
export type StandardId = 'STD-01' | 'STD-02'
export type Tier = 'Tier-1' | 'Tier-2' | 'Tier-3'
export type GraphVersion = 'v0' | 'v1' | 'v2'

export type RequestStatus =
  | 'reviewing'
  | 'awaiting_approval'
  | 'approved'
  | 'rejected'
  | 'auto_approved'
  | 'manual_review'

/** SPEC 3장의 최상위 노드 9개. 서브그래프 내부 노드는 계약상 나오지 않는다. */
export const NODE_NAMES = [
  'parse_request',
  'classify',
  'policy_agent',
  'impact_agent',
  'history_agent',
  'decide',
  'validate',
  'approval_gate',
  'finalize',
] as const
export type NodeName = (typeof NODE_NAMES)[number]
export type NodeRunStatus = 'running' | 'done' | 'failed'

// ---------------------------------------------------------------------------
// app/schemas.py
// ---------------------------------------------------------------------------

export interface ChangeRequest {
  target: string
  change_type: ChangeType
  standard_change_id: StandardId | null
  start: string | null
  end: string | null
  time_precision: TimePrecision
  source: string | null
  port: number | null
  expires_days: number | null
  expires_at: string | null
  has_rollback: boolean | null
  has_test_evidence: boolean | null
  is_emergency: boolean
  ticket: string | null
  reason: string | null
  restart_required: boolean | null
  extra_changes: string[] | null
  cert_expires_at: string | null
}

export interface Violation {
  rule_id: string
  severity: Severity
  evidence: string
}

export interface Recommendation {
  violations: Violation[]
  verdict: Verdict
  risk: Risk | null
  similar_history: string[] | null
  emergency_valid: boolean | null
  post_report_required: boolean
  explanation: string
  conditional_alternative: string | null
  citations: string[]
  warnings: string[]
}

// ---------------------------------------------------------------------------
// 데이터 파일에서 오는 값
// ---------------------------------------------------------------------------

/** change_calendar.json의 scheduled_changes 항목 */
export interface ScheduledChange {
  schedule_id: string
  target: string
  change_type: ChangeType
  title: string
  start: string
  end: string
  owner: string
}

/** impact_agent 결과. id, name, tier, owner_team, depends_on, dependents는 cmdb.json 서비스의 키다. */
export interface Impact {
  id: string
  name: string
  tier: Tier
  owner_team: string
  depends_on: string[]
  dependents: string[]
  same_time_changes: ScheduledChange[]
}

export interface IncidentDetail {
  severity: string
  duration_minutes: number
  impact: string
  root_cause: string
  lesson: string
}

/** history.json 항목 */
export interface HistoryItem {
  history_id: string
  date: string
  service: string
  change_type: ChangeType
  title: string
  description: string
  outcome: '성공' | '장애' | '실패 후 롤백'
  incident: IncidentDetail | null
  related_rules: string[]
}

// ---------------------------------------------------------------------------
// 응답 봉투
// ---------------------------------------------------------------------------

/** 조건부 대안을 항목별로 푼 것. 백엔드가 코드로 계산하고 없을 수 있다. */
export interface AlternativeDiffItem {
  field: string
  label: string
  current: string | number | boolean | null
  suggested: string | number | boolean | null
}

export interface Approval {
  decision: 'approve' | 'reject'
  approver: string
  comment: string
  at: string
}

export interface NodeRun {
  node: NodeName
  status: NodeRunStatus
  started_at: string
  ended_at: string | null
  error: string | null
}

/** GET /api/requests/{id} */
export interface RequestDetail {
  request_id: string
  parent_request_id: string | null
  status: RequestStatus
  version: GraphVersion
  requester: string
  submitted_at: string
  text: string
  route: Route | null
  nodes: NodeRun[]
  structured: ChangeRequest | null
  recommendation: Recommendation | null
  alternative_diff?: AlternativeDiffItem[] | null
  impact: Impact | null
  similar_incidents: HistoryItem[]
  has_similar_incident: boolean
  approval: Approval | null
  errors: string[]
}

/** 목록(GET /api/requests, GET /api/approvals)의 항목 */
export interface RequestSummary {
  request_id: string
  parent_request_id: string | null
  requester: string
  submitted_at: string
  target: string | null
  change_type: ChangeType | null
  start: string | null
  status: RequestStatus
  verdict: Verdict | null
  risk: Risk | null
  has_similar_incident: boolean
  standard_change_id: StandardId | null
  decided_at: string | null
}

export interface RequestListResponse {
  items: RequestSummary[]
}

export type ApprovalListStatus = 'awaiting_approval' | 'auto_approved'

// ---------------------------------------------------------------------------
// 요청과 응답 본문
// ---------------------------------------------------------------------------

export interface CreateRequestBody {
  text: string
  /** 재요청이면 이전 요청 번호. 같은 request_id를 다시 쓰지 않고 새 요청을 만든다. */
  parent_request_id?: string
}

export interface CreateRequestResponse {
  request_id: string
  parent_request_id: string | null
  status: RequestStatus
}

export interface DecisionBody {
  decision: 'approve' | 'reject'
  comment: string
}

export interface DecisionResponse {
  request_id: string
  status: 'approved' | 'rejected'
  approval: Approval
}

export type ApiErrorCode = 'forbidden' | 'not_found' | 'conflict' | 'validation_error' | 'internal'

export interface ApiErrorBody {
  error: { code: ApiErrorCode; message: string }
}

// ---------------------------------------------------------------------------
// SSE 이벤트 6종 (GET /api/requests/{id}/stream)
// ---------------------------------------------------------------------------

export interface NodeStartedEvent {
  node: NodeName
  at: string
}

export interface NodeCompletedEvent {
  node: NodeName
  at: string
  error: string | null
  /** parse_request에서만 */
  structured?: ChangeRequest
  /** classify에서만 */
  route?: Route
}

export interface RecommendationEvent {
  recommendation: Recommendation
  alternative_diff?: AlternativeDiffItem[] | null
  impact: Impact | null
  similar_incidents: HistoryItem[]
  has_similar_incident: boolean
}

export interface AwaitingApprovalEvent {
  request_id: string
  status: 'awaiting_approval'
  at: string
}

export type DoneStatus = 'approved' | 'rejected' | 'auto_approved' | 'manual_review'

export interface DoneEvent {
  request_id: string
  status: DoneStatus
  at: string
  approval: Approval | null
  /** manual_review일 때의 사용자용 문장 */
  message: string | null
}

export interface StreamErrorEvent {
  code: 'internal'
  message: string
}

export interface SseEventMap {
  node_started: NodeStartedEvent
  node_completed: NodeCompletedEvent
  recommendation: RecommendationEvent
  awaiting_approval: AwaitingApprovalEvent
  done: DoneEvent
  error: StreamErrorEvent
}

export type SseEventName = keyof SseEventMap

export const SSE_EVENT_NAMES: readonly SseEventName[] = [
  'node_started',
  'node_completed',
  'recommendation',
  'awaiting_approval',
  'done',
  'error',
]

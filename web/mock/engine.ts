import type {
  AlternativeDiffItem,
  Approval,
  ApprovalListStatus,
  ChangeRequest,
  CreateRequestBody,
  CreateRequestResponse,
  DecisionBody,
  DecisionResponse,
  HistoryItem,
  Impact,
  NodeCompletedEvent,
  NodeName,
  NodeRun,
  Recommendation,
  RequestDetail,
  RequestStatus,
  RequestSummary,
  Risk,
  Role,
  Route,
  SseEventMap,
  SseEventName,
  Violation,
} from '../src/api/types.ts'
import evidence from './evidence.json' with { type: 'json' }
import {
  emergencyValid,
  getDevRequest,
  getHistoryItem,
  hasSimilarIncident,
  impactFor,
  type DevRequest,
} from './data.ts'

// 백엔드(7단계) 없이 docs/API_CONTRACT.md의 응답과 SSE 이벤트를 흉내 내는 목업의 심장부다.
// HTTP와 SSE 연결은 plugin.ts가 맡고, 여기는 상태와 시나리오만 다룬다(그래서 Vitest로 직접 시험한다).
//
// 입력 문장에 따라 시나리오를 고른다.
//   [긴급] 또는 INC-번호 -> REQ-D07 긴급 변경           로그 레벨 -> REQ-D04 표준 변경(자동 승인)
//   인증서 -> REQ-D10 표준 변경(동결에 걸려 반려)       payments-db(B페이먼츠/203.0.113.10) -> REQ-D02 승인
//   payments-db -> REQ-D01 반려                          billing-db 컬럼 -> REQ-D03 조건부 승인
//   billing-db(3306/C정산) -> REQ-D09                    auth-db -> REQ-D08    admin-portal -> REQ-D05
//   order-service/배포 -> REQ-D06 승인                   그 밖 -> 대상 서비스를 찾지 못해 수동 검토
// 재요청(parent_request_id)은 고쳐서 규정에 맞는 요청(승인 권고)으로 돌려준다.
// 본문에 [목업:영향도실패]를 넣으면 영향도만 "확인 불가"로 끝나고(검토는 계속),
// [목업:수동검토]를 넣으면 정책 검사가 "확인 불가"로 끝나 수동 검토로 넘어간다.

export class HttpError extends Error {
  constructor(
    readonly status: number,
    readonly code: 'forbidden' | 'not_found' | 'conflict' | 'validation_error' | 'internal',
    message: string,
  ) {
    super(message)
  }
}

export type FailureMode = 'impact' | 'policy' | 'no_target'

export interface Scenario {
  key: string
  route: Route
  structured: ChangeRequest
  recommendation: Recommendation
  alternative_diff: AlternativeDiffItem[] | null
  impact: Impact | null
  similar_incidents: HistoryItem[]
  has_similar_incident: boolean
  auto_approve: boolean
}

const STRUCTURED_KEYS = [
  'target',
  'change_type',
  'standard_change_id',
  'start',
  'end',
  'time_precision',
  'source',
  'port',
  'expires_days',
  'expires_at',
  'has_rollback',
  'has_test_evidence',
  'is_emergency',
  'ticket',
  'reason',
  'restart_required',
  'extra_changes',
  'cert_expires_at',
] as const satisfies readonly (keyof ChangeRequest)[]

/** 정답 라벨 전용 필드(acceptable_change_types, time_note)를 빼고 ChangeRequest 모양만 남긴다. */
function pickStructured(source: DevRequest['expected']['structured']): ChangeRequest {
  const picked: Record<string, unknown> = {}
  for (const key of STRUCTURED_KEYS) picked[key] = source[key] ?? null
  return picked as unknown as ChangeRequest
}

// ---------------------------------------------------------------------------
// 시나리오
// ---------------------------------------------------------------------------

const scenarioCache = new Map<string, Scenario>()

/** 계약 예시와 같은 "지금 -> 이렇게". REQ-D01만 값을 주고 나머지는 null이라 "문장만" 경로를 확인한다. */
const ALTERNATIVE_DIFF: Record<string, AlternativeDiffItem[]> = {
  'REQ-D01': [
    { field: 'start', label: '작업 시간', current: '2026-10-30T20:00:00+09:00', suggested: '2026-11-03T14:00:00+09:00' },
    { field: 'source', label: '접속할 곳', current: null, suggested: '등록된 파트너 IP /32' },
    { field: 'expires_days', label: '열어 둘 기간', current: null, suggested: 7 },
  ],
}

export function scenarioFor(devId: string): Scenario {
  const cached = scenarioCache.get(devId)
  if (cached) return cached
  const dev = getDevRequest(devId)
  if (!dev) throw new Error(`dev 요청이 없다: ${devId}`)

  const expected = dev.expected
  const structured = pickStructured(expected.structured)
  const route: Route = structured.is_emergency ? 'emergency' : structured.change_type === 'standard' ? 'standard' : 'general'
  const violations = ((evidence as Record<string, unknown>)[devId] as Violation[] | undefined) ?? []
  const isEmergency = route === 'emergency'

  const recommendation: Recommendation = {
    violations,
    verdict: expected.verdict,
    risk: expected.risk,
    similar_history: expected.similar_history,
    emergency_valid: isEmergency ? emergencyValid(structured.ticket, structured.target) : expected.emergency_valid,
    post_report_required: expected.post_report_required,
    explanation: expected.rationale,
    conditional_alternative: expected.conditional_alternative,
    citations: [...violations.map((v) => v.rule_id), ...(isEmergency ? ['CHG-07'] : [])],
    warnings: [],
  }

  const isStandard = route === 'standard'
  const scenario: Scenario = {
    key: devId,
    route,
    structured,
    recommendation,
    alternative_diff: ALTERNATIVE_DIFF[devId] ?? null,
    impact: isStandard ? null : impactFor(structured.target, structured.start, structured.end),
    similar_incidents: isStandard
      ? []
      : (expected.similar_history ?? []).map(getHistoryItem).filter((h): h is HistoryItem => h !== undefined),
    has_similar_incident: isStandard ? false : hasSimilarIncident(structured.target, structured.change_type),
    auto_approve: expected.verdict === '자동 승인',
  }
  scenarioCache.set(devId, scenario)
  return scenario
}

/** 재요청 결과: REQ-D01을 계약 예시의 대안대로 고친 요청이다. 규정에 어긋나는 곳이 없어 승인을 권고한다. */
export function fixedScenario(): Scenario {
  const base = scenarioFor('REQ-D01')
  const structured: ChangeRequest = {
    ...base.structured,
    start: '2026-11-03T14:00:00+09:00',
    end: '2026-11-03T15:00:00+09:00',
    source: '203.0.113.10/32',
    expires_days: 7,
  }
  return {
    ...base,
    key: 'FIXED',
    structured,
    recommendation: {
      ...base.recommendation,
      violations: [],
      verdict: '승인',
      explanation: '등록된 파트너 IP(/32)와 허용 기한 7일을 지정했고, 동결이 끝난 평일 낮이라 규정에 어긋나는 곳이 없다.',
      conditional_alternative: null,
      citations: [],
    },
    alternative_diff: null,
    impact: impactFor(structured.target, structured.start, structured.end),
  }
}

export function pickScenario(text: string, hasParent: boolean): { scenario: Scenario | null; failure: FailureMode | null } {
  const tag: FailureMode | null = text.includes('[목업:수동검토]') ? 'policy' : text.includes('[목업:영향도실패]') ? 'impact' : null

  let devId: string | null = null
  if (/\[긴급\]|INC-\d{4}-\d{4}/.test(text)) devId = 'REQ-D07'
  else if (/로그\s*레벨/.test(text)) devId = 'REQ-D04'
  else if (/인증서/.test(text)) devId = 'REQ-D10'
  else if (text.includes('payments-db')) devId = /B페이먼츠|203\.0\.113\.10/.test(text) ? 'REQ-D02' : 'REQ-D01'
  else if (text.includes('billing-db')) devId = /3306|C정산|198\.51\./.test(text) ? 'REQ-D09' : 'REQ-D03'
  else if (text.includes('auth-db')) devId = 'REQ-D08'
  else if (text.includes('admin-portal')) devId = 'REQ-D05'
  else if (/order-service|배포/.test(text)) devId = 'REQ-D06'

  if (hasParent && tag === null) return { scenario: fixedScenario(), failure: null }
  if (devId === null && tag === null) return { scenario: null, failure: 'no_target' }
  return { scenario: scenarioFor(devId ?? 'REQ-D01'), failure: tag }
}

// ---------------------------------------------------------------------------
// 서버 상태
// ---------------------------------------------------------------------------

export type StoredEvent = { [K in SseEventName]: { event: K; data: SseEventMap[K] } }[SseEventName]

const DELAYS: Record<'parse_request' | 'classify' | 'policy_agent' | 'impact_agent' | 'history_agent' | 'decide' | 'validate', number> = {
  parse_request: 900,
  classify: 500,
  policy_agent: 1400,
  impact_agent: 2400,
  history_agent: 3200,
  decide: 1500,
  validate: 900,
}

const RISK_RANK: Record<Risk, number> = { 높음: 0, 중간: 1, 낮음: 2 }
const SEED_DEV_IDS = ['REQ-D02', 'REQ-D04', 'REQ-D05', 'REQ-D06', 'REQ-D07', 'REQ-D08', 'REQ-D09', 'REQ-D10']

interface Rec {
  request_id: string
  parent_request_id: string | null
  requester: string
  submitted_at: string
  text: string
  status: RequestStatus
  route: Route | null
  nodes: NodeRun[]
  structured: ChangeRequest | null
  recommendation: Recommendation | null
  alternative_diff: AlternativeDiffItem[] | null
  impact: Impact | null
  similar_incidents: HistoryItem[]
  has_similar_incident: boolean
  approval: Approval | null
  errors: string[]
  decided_at: string | null
  /** 서버 메모리의 이벤트 기록. 시드 데이터는 비어 있다(서버 재시작 뒤와 같은 상황). */
  events: StoredEvent[]
  listeners: Set<(event: StoredEvent) => void>
}

export interface MockServerOptions {
  /** 지연 배율. 1이 기본, 0이면 기다리지 않는다(테스트). */
  speed?: number
  now?: () => Date
}

export interface DemoUser {
  role: Role
  name: string
}

function kstIso(date: Date): string {
  const shifted = new Date(date.getTime() + 9 * 3600 * 1000)
  return shifted.toISOString().replace(/\.\d{3}Z$/, '+09:00')
}

/** 본문의 비밀값을 가린다(계약: text는 마스킹한 본문). */
export function maskSecrets(text: string): string {
  return text
    .replace(/(password|passwd|secret|token|api[_-]?key)(\s*[=:]\s*)\S+/gi, '$1$2****')
    .replace(/AKIA[0-9A-Z]{16}/g, '****')
}

export class MockServer {
  private readonly recs = new Map<string, Rec>()
  private readonly running = new Map<string, Promise<void>>()
  private readonly speed: number
  private readonly now: () => Date
  private seq = 180

  constructor(options: MockServerOptions = {}) {
    this.speed = options.speed ?? 1
    this.now = options.now ?? (() => new Date())
    this.seed()
  }

  // --- 시드 -----------------------------------------------------------------

  private nextId(): string {
    this.seq += 1
    return `REQ-${String(this.seq).padStart(4, '0')}`
  }

  private seed(): void {
    const base = this.now().getTime() - 3 * 3600 * 1000
    SEED_DEV_IDS.forEach((devId, index) => {
      const dev = getDevRequest(devId)
      if (!dev) return
      const sc = scenarioFor(devId)
      const submittedMs = base + index * 17 * 60 * 1000
      const auto = sc.auto_approve
      const rec: Rec = {
        request_id: this.nextId(),
        parent_request_id: null,
        requester: dev.requester,
        submitted_at: kstIso(new Date(submittedMs)),
        text: maskSecrets(dev.text),
        status: auto ? 'auto_approved' : 'awaiting_approval',
        route: sc.route,
        nodes: this.seedNodes(sc, submittedMs, auto),
        structured: sc.structured,
        recommendation: sc.recommendation,
        alternative_diff: sc.alternative_diff,
        impact: sc.impact,
        similar_incidents: sc.similar_incidents,
        has_similar_incident: sc.has_similar_incident,
        approval: null,
        errors: [],
        decided_at: auto ? kstIso(new Date(submittedMs + 60 * 1000)) : null,
        events: [],
        listeners: new Set(),
      }
      this.recs.set(rec.request_id, rec)
    })
  }

  private seedNodes(sc: Scenario, startMs: number, auto: boolean): NodeRun[] {
    const names: NodeName[] =
      sc.route === 'standard'
        ? ['parse_request', 'classify', 'policy_agent', 'decide', 'validate', 'approval_gate']
        : ['parse_request', 'classify', 'policy_agent', 'impact_agent', 'history_agent', 'decide', 'validate', 'approval_gate']
    return names.map((node, i) => {
      const gate = node === 'approval_gate'
      const running = gate && !auto
      return {
        node,
        status: running ? 'running' : 'done',
        started_at: kstIso(new Date(startMs + i * 2000)),
        ended_at: running ? null : kstIso(new Date(startMs + i * 2000 + 1500)),
        error: null,
      }
    })
  }

  // --- 요청 접수와 실행 -------------------------------------------------------

  createRequest(user: DemoUser, body: CreateRequestBody): CreateRequestResponse {
    if (user.role !== 'requester') throw new HttpError(403, 'forbidden', '이 작업을 할 수 있는 권한이 없어요.')
    const text = typeof body.text === 'string' ? body.text.trim() : ''
    if (!text || text.length > 4000) {
      throw new HttpError(422, 'validation_error', '변경 내용은 1자 이상 4000자 이하로 적어 주세요.')
    }
    const parentId = body.parent_request_id ?? null
    if (parentId !== null && !this.recs.has(parentId)) {
      throw new HttpError(404, 'not_found', '이전 요청을 찾을 수 없어요.')
    }

    const { scenario, failure } = pickScenario(text, parentId !== null)
    const rec: Rec = {
      request_id: this.nextId(),
      parent_request_id: parentId,
      requester: user.name,
      submitted_at: kstIso(this.now()),
      text: maskSecrets(text),
      status: 'reviewing',
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
      decided_at: null,
      events: [],
      listeners: new Set(),
    }
    this.recs.set(rec.request_id, rec)
    this.running.set(rec.request_id, this.run(rec, scenario, failure))
    return { request_id: rec.request_id, parent_request_id: parentId, status: 'reviewing' }
  }

  /** 테스트용: 실행이 끝(승인 대기, 완료, 수동 검토)까지 가기를 기다린다. */
  whenSettled(id: string): Promise<void> {
    return this.running.get(id) ?? Promise.resolve()
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms * this.speed))
  }

  private stamp(): string {
    return kstIso(this.now())
  }

  private emit(rec: Rec, stored: StoredEvent): void {
    rec.events.push(stored)
    for (const listener of rec.listeners) listener(stored)
  }

  private startNode(rec: Rec, node: NodeName): void {
    const at = this.stamp()
    rec.nodes.push({ node, status: 'running', started_at: at, ended_at: null, error: null })
    this.emit(rec, { event: 'node_started', data: { node, at } })
  }

  private completeNode(rec: Rec, node: NodeName, extra: Partial<NodeCompletedEvent> = {}, error: string | null = null): void {
    const at = this.stamp()
    const run = rec.nodes.find((n) => n.node === node)
    if (run) {
      run.status = error ? 'failed' : 'done'
      run.ended_at = at
      run.error = error
    }
    if (error) rec.errors.push(error)
    this.emit(rec, { event: 'node_completed', data: { node, at, error, ...extra } })
  }

  private finishManual(rec: Rec, message: string): void {
    const at = this.stamp()
    rec.status = 'manual_review'
    rec.decided_at = at
    this.emit(rec, { event: 'done', data: { request_id: rec.request_id, status: 'manual_review', at, approval: null, message } })
  }

  private async run(rec: Rec, sc: Scenario | null, failure: FailureMode | null): Promise<void> {
    await this.sleep(300)

    this.startNode(rec, 'parse_request')
    await this.sleep(DELAYS.parse_request)
    if (sc === null) {
      this.completeNode(rec, 'parse_request', {}, '요청에서 대상 서비스를 찾지 못했어요.')
      this.finishManual(rec, '요청에서 대상 서비스를 찾지 못해 수동 검토로 넘깁니다.')
      return
    }
    rec.structured = sc.structured
    this.completeNode(rec, 'parse_request', { structured: sc.structured })

    this.startNode(rec, 'classify')
    await this.sleep(DELAYS.classify)
    rec.route = sc.route
    this.completeNode(rec, 'classify', { route: sc.route })

    // 병렬 검사: 표준 변경은 정책 검사만, 나머지는 세 갈래를 같은 시각에 시작한다.
    const branches: Array<'policy_agent' | 'impact_agent' | 'history_agent'> =
      sc.route === 'standard' ? ['policy_agent'] : ['policy_agent', 'impact_agent', 'history_agent']
    for (const node of branches) this.startNode(rec, node)
    await Promise.all(
      branches.map(async (node) => {
        await this.sleep(DELAYS[node])
        if (node === 'policy_agent' && failure === 'policy') this.completeNode(rec, node, {}, '정책 검사: 확인 불가')
        else if (node === 'impact_agent' && failure === 'impact') this.completeNode(rec, node, {}, '영향도: 확인 불가')
        else this.completeNode(rec, node)
      }),
    )
    if (failure === 'policy') {
      this.finishManual(rec, '정책 검사를 확인하지 못해 수동 검토로 넘깁니다.')
      return
    }

    this.startNode(rec, 'decide')
    await this.sleep(DELAYS.decide)
    this.completeNode(rec, 'decide')

    this.startNode(rec, 'validate')
    await this.sleep(DELAYS.validate)
    this.completeNode(rec, 'validate')

    rec.recommendation = sc.recommendation
    rec.alternative_diff = sc.alternative_diff
    rec.impact = failure === 'impact' ? null : sc.impact
    rec.similar_incidents = sc.similar_incidents
    rec.has_similar_incident = sc.has_similar_incident
    this.emit(rec, {
      event: 'recommendation',
      data: {
        recommendation: sc.recommendation,
        alternative_diff: sc.alternative_diff,
        impact: rec.impact,
        similar_incidents: sc.similar_incidents,
        has_similar_incident: sc.has_similar_incident,
      },
    })

    this.startNode(rec, 'approval_gate')
    if (sc.auto_approve) {
      await this.sleep(300)
      this.completeNode(rec, 'approval_gate')
      this.startNode(rec, 'finalize')
      this.completeNode(rec, 'finalize')
      const at = this.stamp()
      rec.status = 'auto_approved'
      rec.decided_at = at
      this.emit(rec, { event: 'done', data: { request_id: rec.request_id, status: 'auto_approved', at, approval: null, message: null } })
      return
    }
    rec.status = 'awaiting_approval'
    this.emit(rec, { event: 'awaiting_approval', data: { request_id: rec.request_id, status: 'awaiting_approval', at: this.stamp() } })
  }

  // --- 결정 ---------------------------------------------------------------------

  decide(id: string, user: DemoUser, body: DecisionBody): DecisionResponse {
    if (user.role !== 'approver') throw new HttpError(403, 'forbidden', '이 작업을 할 수 있는 권한이 없어요.')
    const rec = this.mustGet(id)
    if (body.decision !== 'approve' && body.decision !== 'reject') {
      throw new HttpError(422, 'validation_error', '결정은 승인 또는 반려여야 해요.')
    }
    const comment = body.comment ?? ''
    if (typeof comment !== 'string' || comment.length > 1000) {
      throw new HttpError(422, 'validation_error', '코멘트는 1000자 이하로 적어 주세요.')
    }
    if (rec.status !== 'awaiting_approval') throw new HttpError(409, 'conflict', '승인 대기 중인 요청이 아니에요.')

    const approval: Approval = { decision: body.decision, approver: user.name, comment, at: this.stamp() }
    rec.approval = approval
    rec.status = body.decision === 'approve' ? 'approved' : 'rejected'
    rec.decided_at = approval.at
    if (rec.nodes.some((n) => n.node === 'approval_gate' && n.status === 'running')) this.completeNode(rec, 'approval_gate')
    this.startNode(rec, 'finalize')
    this.completeNode(rec, 'finalize')
    this.emit(rec, {
      event: 'done',
      data: { request_id: rec.request_id, status: rec.status as 'approved' | 'rejected', at: approval.at, approval, message: null },
    })
    return { request_id: rec.request_id, status: rec.status as 'approved' | 'rejected', approval }
  }

  // --- 조회 ---------------------------------------------------------------------

  private mustGet(id: string): Rec {
    const rec = this.recs.get(id)
    if (!rec) throw new HttpError(404, 'not_found', '요청을 찾을 수 없어요.')
    return rec
  }

  private summary(rec: Rec): RequestSummary {
    return {
      request_id: rec.request_id,
      parent_request_id: rec.parent_request_id,
      requester: rec.requester,
      submitted_at: rec.submitted_at,
      target: rec.structured?.target ?? null,
      change_type: rec.structured?.change_type ?? null,
      start: rec.structured?.start ?? null,
      status: rec.status,
      verdict: rec.recommendation?.verdict ?? null,
      risk: rec.recommendation?.risk ?? null,
      has_similar_incident: rec.has_similar_incident,
      standard_change_id: rec.structured?.standard_change_id ?? null,
      decided_at: rec.decided_at,
    }
  }

  /** GET /api/requests: 요청자는 본인 요청만, 승인자는 전부. 최신순. */
  listRequests(user: DemoUser): RequestSummary[] {
    return [...this.recs.values()]
      .filter((rec) => user.role === 'approver' || rec.requester === user.name)
      .sort((a, b) => b.submitted_at.localeCompare(a.submitted_at))
      .map((rec) => this.summary(rec))
  }

  /** GET /api/approvals: 위험도 높은 순 -> 유사 장애 있음 먼저 -> 최신순. */
  listApprovals(user: DemoUser, status: ApprovalListStatus = 'awaiting_approval'): RequestSummary[] {
    if (user.role !== 'approver') throw new HttpError(403, 'forbidden', '이 작업을 할 수 있는 권한이 없어요.')
    return [...this.recs.values()]
      .filter((rec) => rec.status === status)
      .sort((a, b) => {
        const riskA = a.recommendation?.risk ? RISK_RANK[a.recommendation.risk] : 3
        const riskB = b.recommendation?.risk ? RISK_RANK[b.recommendation.risk] : 3
        if (riskA !== riskB) return riskA - riskB
        if (a.has_similar_incident !== b.has_similar_incident) return a.has_similar_incident ? -1 : 1
        return b.submitted_at.localeCompare(a.submitted_at)
      })
      .map((rec) => this.summary(rec))
  }

  getDetail(id: string): RequestDetail {
    const rec = this.mustGet(id)
    return {
      request_id: rec.request_id,
      parent_request_id: rec.parent_request_id,
      status: rec.status,
      version: 'v2',
      requester: rec.requester,
      submitted_at: rec.submitted_at,
      text: rec.text,
      route: rec.route,
      nodes: rec.nodes,
      structured: rec.structured,
      recommendation: rec.recommendation,
      alternative_diff: rec.alternative_diff,
      impact: rec.impact,
      similar_incidents: rec.similar_incidents,
      has_similar_incident: rec.has_similar_incident,
      approval: rec.approval,
      errors: rec.errors,
    }
  }

  // --- 스트림 -------------------------------------------------------------------

  hasRequest(id: string): boolean {
    return this.recs.has(id)
  }

  eventsOf(id: string): StoredEvent[] {
    return this.mustGet(id).events
  }

  subscribe(id: string, listener: (event: StoredEvent) => void): () => void {
    const rec = this.mustGet(id)
    rec.listeners.add(listener)
    return () => rec.listeners.delete(listener)
  }

  /**
   * 이벤트 기록이 없을 때(서버 재시작 뒤와 같은 상황) 현재의 종료 이벤트 하나를 만든다.
   * 아직 검토 중이면 null이라 호출한 쪽이 새 이벤트를 기다린다.
   */
  terminalEvent(id: string): StoredEvent | null {
    const rec = this.mustGet(id)
    const at = rec.decided_at ?? rec.submitted_at
    if (rec.status === 'awaiting_approval') {
      return { event: 'awaiting_approval', data: { request_id: id, status: 'awaiting_approval', at } }
    }
    if (rec.status === 'reviewing') return null
    return {
      event: 'done',
      data: {
        request_id: id,
        status: rec.status,
        at,
        approval: rec.approval,
        message: rec.status === 'manual_review' ? '수동 검토로 넘겼어요.' : null,
      },
    }
  }
}

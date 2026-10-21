import { describe, expect, it } from 'vitest'
import { MockServer, type DemoUser } from '../mock/engine'
import type { ChangeRequest, NodeRun, RequestDetail } from '../src/api/types'
import { applyEvent, buildRetryText, initialDetail, timelineSteps, understoodRows } from '../src/lib/review'

const requester: DemoUser = { role: 'requester', name: '김운영' }
const D01_TEXT = '이번 주 금요일 저녁 8시에 payments-db 방화벽 좀 열어주세요. 외부 파트너사가 접속해야 해요.'

async function runMock(text: string, parent?: string) {
  const server = new MockServer({ speed: 0 })
  const created = server.createRequest(requester, { text, parent_request_id: parent })
  await server.whenSettled(created.request_id)
  return { server, created }
}

/** 서버가 보낸 이벤트를 처음부터 끝까지 접어서 서버의 상세 응답과 같아지는지 본다. */
async function foldAll(text: string) {
  const { server, created } = await runMock(text)
  let folded = initialDetail(created, { text, requester: requester.name, submitted_at: server.getDetail(created.request_id).submitted_at })
  for (const stored of server.eventsOf(created.request_id)) folded = applyEvent(folded, stored.event, stored.data as never)
  return { folded, truth: server.getDetail(created.request_id) as RequestDetail }
}

describe('이벤트 접기', () => {
  it('일반 변경: 이벤트를 모두 접으면 서버의 상세 응답과 같다', async () => {
    const { folded, truth } = await foldAll(D01_TEXT)

    expect(folded.status).toBe(truth.status)
    expect(folded.route).toBe(truth.route)
    expect(folded.nodes.map((n) => [n.node, n.status])).toEqual(truth.nodes.map((n) => [n.node, n.status]))
    expect(folded.structured).toEqual(truth.structured)
    expect(folded.recommendation).toEqual(truth.recommendation)
    expect(folded.alternative_diff).toEqual(truth.alternative_diff)
    expect(folded.impact).toEqual(truth.impact)
    expect(folded.similar_incidents).toEqual(truth.similar_incidents)
    expect(folded.has_similar_incident).toBe(truth.has_similar_incident)
  })

  it('표준 변경: 자동 승인으로 끝난다', async () => {
    const { folded, truth } = await foldAll('auth-service 로그 레벨을 INFO에서 DEBUG로 올릴게요. 재시작은 없어요.')

    expect(folded.status).toBe('auto_approved')
    expect(folded.nodes.map((n) => n.node)).toEqual(truth.nodes.map((n) => n.node))
    expect(folded.recommendation).toEqual(truth.recommendation)
  })

  it('수동 검토: done의 message가 남고 확인 불가 문장이 errors에 쌓인다', async () => {
    const { folded, truth } = await foldAll(`${D01_TEXT} [목업:수동검토]`)

    expect(folded.status).toBe('manual_review')
    expect(folded.message).toBeTruthy()
    expect(folded.errors).toEqual(truth.errors)
    expect(folded.recommendation).toBeNull()
  })

  it('서버 error 이벤트도 수동 검토로 보고 문장을 남긴다', () => {
    const base = initialDetail({ request_id: 'REQ-1', parent_request_id: null, status: 'reviewing' }, { text: 't', requester: '김운영', submitted_at: '2026-10-29T16:00:00+09:00' })
    const next = applyEvent(base, 'error', { code: 'internal', message: '자동 검토를 완료하지 못해 수동 검토로 넘깁니다.' })
    expect(next.status).toBe('manual_review')
    expect(next.message).toBe('자동 검토를 완료하지 못해 수동 검토로 넘깁니다.')
  })

  it('접기는 원본을 바꾸지 않는다', () => {
    const base = initialDetail({ request_id: 'REQ-1', parent_request_id: null, status: 'reviewing' }, { text: 't', requester: '김운영', submitted_at: '2026-10-29T16:00:00+09:00' })
    const next = applyEvent(base, 'node_started', { node: 'parse_request', at: '2026-10-29T16:00:01+09:00' })
    expect(base.nodes).toEqual([])
    expect(next.nodes).toHaveLength(1)
    // 같은 노드를 다시 시작해도(replay) 중복으로 쌓지 않는다
    expect(applyEvent(next, 'node_started', { node: 'parse_request', at: '2026-10-29T16:00:01+09:00' }).nodes).toHaveLength(1)
  })
})

const run = (node: NodeRun['node'], status: NodeRun['status']): NodeRun => ({
  node,
  status,
  started_at: '2026-10-29T16:00:00+09:00',
  ended_at: status === 'running' ? null : '2026-10-29T16:00:01+09:00',
  error: status === 'failed' ? '확인 불가' : null,
})

describe('타임라인 노드 상태', () => {
  const stateOf = (steps: ReturnType<typeof timelineSteps>) => Object.fromEntries(steps.map((s) => [s.node, s.state]))

  it('검토 중: 시작 기록이 없는 노드는 대기다', () => {
    const steps = timelineSteps([run('parse_request', 'done'), run('classify', 'running')], 'reviewing')
    expect(stateOf(steps)).toMatchObject({ parse_request: 'done', classify: 'running', policy_agent: 'waiting', approval_gate: 'waiting' })
    expect(steps.find((s) => s.node === 'classify')?.text).toBe('확인 중')
  })

  it('정상 종료(표준 변경): impact와 history는 건너뜀이다', () => {
    const nodes = (['parse_request', 'classify', 'policy_agent', 'decide', 'validate', 'approval_gate'] as const).map((n) => run(n, 'done'))
    const steps = timelineSteps(nodes, 'auto_approved')
    expect(stateOf(steps)).toMatchObject({ impact_agent: 'skipped', history_agent: 'skipped', policy_agent: 'done', approval_gate: 'done' })
    expect(steps.find((s) => s.node === 'impact_agent')?.text).toBe('건너뜀')
  })

  it('정상 종료(v1): history와 validate가 건너뜀이다', () => {
    const nodes = (['parse_request', 'classify', 'policy_agent', 'impact_agent', 'decide'] as const).map((n) => run(n, 'done'))
    const steps = timelineSteps([...nodes, run('approval_gate', 'running')], 'awaiting_approval')
    expect(stateOf(steps)).toMatchObject({ history_agent: 'skipped', validate: 'skipped', approval_gate: 'running' })
    expect(steps.find((s) => s.node === 'approval_gate')?.text).toBe('결재 대기 중')
  })

  it('수동 검토로 끝나면 시작 안 한 노드는 건너뜀이 아니라 진행 안 됨이다', () => {
    const nodes = [run('parse_request', 'done'), run('classify', 'done'), run('policy_agent', 'failed'), run('impact_agent', 'done'), run('history_agent', 'done')]
    const steps = timelineSteps(nodes, 'manual_review')
    expect(stateOf(steps)).toMatchObject({
      policy_agent: 'failed',
      decide: 'notrun',
      validate: 'notrun',
      approval_gate: 'notrun',
    })
    expect(steps.find((s) => s.node === 'policy_agent')?.text).toBe('확인 못 했어요')
    expect(steps.find((s) => s.node === 'decide')?.text).toBe('진행 안 됨')
  })

  it('승인자 결재 노드는 정상 종료여도 시작 기록이 없으면 건너뜀이 아니라 대기다', () => {
    const steps = timelineSteps([run('parse_request', 'done')], 'awaiting_approval')
    expect(stateOf(steps).approval_gate).toBe('waiting')
  })

  it('항상 화면 노드 8개를 고정 순서로 돌려준다', () => {
    expect(timelineSteps([], 'reviewing').map((s) => s.node)).toEqual([
      'parse_request',
      'classify',
      'policy_agent',
      'impact_agent',
      'history_agent',
      'decide',
      'validate',
      'approval_gate',
    ])
  })
})

const firewall: ChangeRequest = {
  target: 'payments-db',
  change_type: 'firewall',
  standard_change_id: null,
  start: '2026-10-30T20:00:00+09:00',
  end: '2026-10-30T21:00:00+09:00',
  time_precision: 'exact',
  source: null,
  port: null,
  expires_days: null,
  expires_at: null,
  has_rollback: null,
  has_test_evidence: null,
  is_emergency: false,
  ticket: null,
  reason: '외부 파트너사 접속',
  restart_required: null,
  extra_changes: null,
  cert_expires_at: null,
}

describe('이렇게 이해했어요', () => {
  it('방화벽: 출발지와 허용 기한이 없으면 빠졌어요로 표시한다', () => {
    const rows = understoodRows(firewall, '결제 DB (PostgreSQL)')
    const byLabel = Object.fromEntries(rows.map((r) => [r.label, r]))

    expect(byLabel['서비스'].value).toBe('payments-db (결제 DB (PostgreSQL))')
    expect(byLabel['시간'].value).toBe('10/30(금) 오후 8시 ~ 9시')
    expect(byLabel['접속할 곳(IP)']).toMatchObject({ value: '적지 않았어요', missing: true })
    expect(byLabel['열어 둘 기간']).toMatchObject({ value: '적지 않았어요', missing: true })
    expect(byLabel['접속 포트']).toMatchObject({ value: '서비스 기본값' })
    expect(byLabel['접속 포트'].missing).toBeUndefined()
    expect(byLabel['이유'].value).toBe('외부 파트너사 접속')
  })

  it('사내망 출발지면 허용 기한이 없어도 빠진 것으로 보지 않는다', () => {
    const rows = understoodRows({ ...firewall, source: '10.20.0.0/16', port: 5432 })
    const expiry = rows.find((r) => r.label === '열어 둘 기간')
    expect(expiry?.missing).toBeFalsy()
  })

  it('값이 다 있으면 빠진 항목이 없다', () => {
    const rows = understoodRows({ ...firewall, source: '203.0.113.10/32', port: 5432, expires_days: 7 })
    expect(rows.some((r) => r.missing)).toBe(false)
    expect(rows.find((r) => r.label === '열어 둘 기간')?.value).toBe('7일')
  })

  it('배포와 DB 변경은 되돌리는 방법과 테스트 결과를 본다', () => {
    const rows = understoodRows({ ...firewall, change_type: 'db_change', target: 'billing-db', has_rollback: false, has_test_evidence: true })
    expect(rows.find((r) => r.label === '되돌리는 방법')).toMatchObject({ missing: true })
    expect(rows.find((r) => r.label === '테스트 결과')).toMatchObject({ value: '붙어 있어요' })
    expect(rows.some((r) => r.label === '접속할 곳(IP)')).toBe(false)
  })
})

describe('다시 요청 본문', () => {
  it('항목별 대안이 있으면 그 값으로 만든다', () => {
    const text = buildRetryText({
      recommendation: null,
      alternative_diff: [
        { field: 'start', label: '작업 시간', current: '2026-10-30T20:00:00+09:00', suggested: '2026-11-03T14:00:00+09:00' },
        { field: 'source', label: '접속할 곳', current: null, suggested: '등록된 파트너 IP /32' },
        { field: 'expires_days', label: '열어 둘 기간', current: null, suggested: 7 },
      ],
    })
    expect(text).toBe('고친 내용으로 다시 요청합니다. 작업 시간: 11/3(화) 오후 2시 / 접속할 곳: 등록된 파트너 IP /32 / 열어 둘 기간: 7일')
  })

  it('항목별 대안이 없으면 대안 문장을 쓴다', () => {
    const text = buildRetryText({
      alternative_diff: null,
      recommendation: { conditional_alternative: '허용 기한을 30일 이내로 줄여 승인' } as never,
    })
    expect(text).toBe('고친 내용으로 다시 요청합니다. 허용 기한을 30일 이내로 줄여 승인')
  })

  it('보낼 만한 대안이 없으면 null이다', () => {
    expect(buildRetryText({ alternative_diff: null, recommendation: { conditional_alternative: null } as never })).toBeNull()
    expect(buildRetryText({ alternative_diff: [], recommendation: null })).toBeNull()
  })
})

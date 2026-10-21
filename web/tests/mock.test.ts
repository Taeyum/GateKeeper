import { describe, expect, it } from 'vitest'
import { hasSimilarIncident } from '../mock/data'
import { HttpError, MockServer, maskSecrets, pickScenario } from '../mock/engine'
import type { DemoUser } from '../mock/engine'

const requester: DemoUser = { role: 'requester', name: '김운영' }
const approver: DemoUser = { role: 'approver', name: '박CAB' }

const D01_TEXT = '이번 주 금요일 저녁 8시에 payments-db 방화벽 좀 열어주세요. 외부 파트너사가 접속해야 해요.'

function newServer() {
  return new MockServer({ speed: 0 })
}

async function submit(server: MockServer, text: string, parent?: string) {
  const { request_id } = server.createRequest(requester, { text, parent_request_id: parent })
  await server.whenSettled(request_id)
  return request_id
}

const nodeNames = (server: MockServer, id: string) => server.getDetail(id).nodes.map((n) => n.node)
const eventNames = (server: MockServer, id: string) => server.eventsOf(id).map((e) => e.event)

describe('유사 장애 있음의 정의', () => {
  it('같은 서비스 + 같은 유형 + 장애 이력이 있을 때만 true다', () => {
    expect(hasSimilarIncident('payments-db', 'firewall')).toBe(true) // HIS-049
    expect(hasSimilarIncident('billing-db', 'db_change')).toBe(true) // HIS-034
    expect(hasSimilarIncident('admin-portal', 'firewall')).toBe(false)
    // 같은 서비스여도 유형이 다르면 아니다
    expect(hasSimilarIncident('payments-db', 'deploy')).toBe(false)
  })
})

describe('시나리오 고르기', () => {
  it('입력 문장의 키워드로 dev 시나리오를 고른다', () => {
    expect(pickScenario(D01_TEXT, false).scenario?.key).toBe('REQ-D01')
    expect(pickScenario('payments-db 5432를 B페이먼츠 IP에서만 열어주세요', false).scenario?.key).toBe('REQ-D02')
    expect(pickScenario('billing-db 청구 테이블에 컬럼 추가합니다', false).scenario?.key).toBe('REQ-D03')
    expect(pickScenario('auth-service 로그 레벨을 DEBUG로 올릴게요', false).scenario?.key).toBe('REQ-D04')
    expect(pickScenario('[긴급] payments-service 설정 변경, INC-2026-0412 대응', false).scenario?.key).toBe('REQ-D07')
  })

  it('대상을 알 수 없으면 시나리오 없이 no_target으로 본다', () => {
    expect(pickScenario('뭔가 좀 열어주세요', false)).toEqual({ scenario: null, failure: 'no_target' })
  })

  it('재요청은 고친 요청(승인 권고)으로 돌려준다', () => {
    const { scenario } = pickScenario('등록된 파트너 IP로 11/3에 7일만 열어주세요', true)
    expect(scenario?.key).toBe('FIXED')
    expect(scenario?.recommendation.verdict).toBe('승인')
    expect(scenario?.recommendation.violations).toEqual([])
  })
})

describe('검토 실행과 이벤트', () => {
  it('일반 변경(REQ-D01)은 8개 노드를 모두 돌고 승인 대기에서 멈춘다', async () => {
    const server = newServer()
    const id = await submit(server, D01_TEXT)
    const detail = server.getDetail(id)

    expect(detail.status).toBe('awaiting_approval')
    expect(detail.route).toBe('general')
    expect(nodeNames(server, id)).toEqual([
      'parse_request',
      'classify',
      'policy_agent',
      'impact_agent',
      'history_agent',
      'decide',
      'validate',
      'approval_gate',
    ])
    expect(detail.nodes.at(-1)).toMatchObject({ node: 'approval_gate', status: 'running' })
    expect(detail.recommendation?.verdict).toBe('반려')
    expect(detail.recommendation?.violations.map((v) => v.rule_id)).toEqual(['CHG-01', 'CHG-02', 'CHG-03', 'CHG-09', 'CHG-10'])
    expect(detail.has_similar_incident).toBe(true)
    expect(detail.alternative_diff?.map((d) => d.field)).toEqual(['start', 'source', 'expires_days'])
    expect(detail.impact).toMatchObject({ id: 'payments-db', tier: 'Tier-1', owner_team: 'DBA팀' })
    expect(detail.similar_incidents.map((h) => h.history_id)).toEqual(['HIS-049'])
    // structured는 ChangeRequest 모양만 있다(정답 라벨 전용 필드가 새지 않는다)
    expect(detail.structured).not.toHaveProperty('time_note')
    expect(detail.structured).not.toHaveProperty('acceptable_change_types')
  })

  it('recommendation 이벤트는 approval_gate 시작 전에 오고, 마지막 이벤트는 awaiting_approval이다', async () => {
    const server = newServer()
    const id = await submit(server, D01_TEXT)
    const events = eventNames(server, id)

    expect(events.at(-1)).toBe('awaiting_approval')
    expect(events.indexOf('recommendation')).toBeGreaterThan(events.lastIndexOf('node_completed') - 1)
    expect(events.filter((e) => e === 'awaiting_approval')).toHaveLength(1)
    const parse = server.eventsOf(id).find((e) => e.event === 'node_completed' && e.data.node === 'parse_request')
    expect(parse?.event === 'node_completed' && parse.data.structured?.target).toBe('payments-db')
  })

  it('병렬 검사 세 노드는 같은 구간에서 모두 시작한 뒤 완료된다', async () => {
    const server = newServer()
    const id = await submit(server, D01_TEXT)
    const names = server.eventsOf(id).map((e) => (e.event === 'node_started' || e.event === 'node_completed' ? `${e.event}:${e.data.node}` : e.event))
    const firstCompleted = names.findIndex((n) => n.startsWith('node_completed:policy_agent'))
    for (const node of ['policy_agent', 'impact_agent', 'history_agent']) {
      expect(names.indexOf(`node_started:${node}`)).toBeLessThan(firstCompleted)
    }
  })

  it('표준 변경(REQ-D04)은 impact/history를 건너뛰고 자동 승인으로 끝난다', async () => {
    const server = newServer()
    const id = await submit(server, 'auth-service 로그 레벨을 INFO에서 DEBUG로 올릴게요. 재시작은 없어요.')
    const detail = server.getDetail(id)

    expect(detail.status).toBe('auto_approved')
    expect(detail.route).toBe('standard')
    expect(nodeNames(server, id)).not.toContain('impact_agent')
    expect(nodeNames(server, id)).not.toContain('history_agent')
    expect(detail.impact).toBeNull()
    expect(detail.similar_incidents).toEqual([])
    expect(detail.has_similar_incident).toBe(false)
    expect(eventNames(server, id).at(-1)).toBe('done')
    expect(server.eventsOf(id).at(-1)).toMatchObject({ data: { status: 'auto_approved', approval: null } })
  })

  it('대상을 못 찾으면 parse_request가 확인 불가로 끝나고 수동 검토로 넘어간다', async () => {
    const server = newServer()
    const id = await submit(server, '뭔가 좀 열어주세요')
    const detail = server.getDetail(id)

    expect(detail.status).toBe('manual_review')
    expect(nodeNames(server, id)).toEqual(['parse_request'])
    expect(detail.nodes[0]).toMatchObject({ status: 'failed' })
    const last = server.eventsOf(id).at(-1)
    expect(last?.event).toBe('done')
    expect(last?.event === 'done' && last.data).toMatchObject({ status: 'manual_review' })
    expect(last?.event === 'done' && last.data.message).toBeTruthy()
  })

  it('[목업:수동검토]는 정책 검사가 확인 불가로 끝나 decide부터는 시작하지 않는다', async () => {
    const server = newServer()
    const id = await submit(server, `${D01_TEXT} [목업:수동검토]`)
    const detail = server.getDetail(id)

    expect(detail.status).toBe('manual_review')
    expect(nodeNames(server, id)).toEqual(['parse_request', 'classify', 'policy_agent', 'impact_agent', 'history_agent'])
    expect(detail.nodes.find((n) => n.node === 'policy_agent')).toMatchObject({ status: 'failed', error: '정책 검사: 확인 불가' })
    expect(detail.errors).toEqual(['정책 검사: 확인 불가'])
  })

  it('[목업:영향도실패]는 영향도만 확인 불가로 끝나고 검토는 계속된다', async () => {
    const server = newServer()
    const id = await submit(server, `${D01_TEXT} [목업:영향도실패]`)
    const detail = server.getDetail(id)

    expect(detail.status).toBe('awaiting_approval')
    expect(detail.impact).toBeNull()
    expect(detail.nodes.find((n) => n.node === 'impact_agent')).toMatchObject({ status: 'failed', error: '영향도: 확인 불가' })
    expect(detail.recommendation?.verdict).toBe('반려')
  })

  it('시스템 설명은 규정 번호 없이 쉬운 말이다', async () => {
    const server = newServer()
    const id = await submit(server, D01_TEXT)
    const explanation = server.getDetail(id).recommendation?.explanation ?? ''
    expect(explanation).toBe('접속할 곳과 기간을 정하지 않은 채, 금요일 밤 정산 기간에 결제 DB를 외부에 열려는 요청이에요.')
    for (const item of server.listRequests(approver)) {
      const text = server.getDetail(item.request_id).recommendation?.explanation ?? ''
      expect(text, item.request_id).not.toMatch(/CHG-\d+|FRZ-\d+|0\.0\.0\.0/)
    }
  })

  it('비밀값은 마스킹한 본문으로 저장한다', async () => {
    const server = newServer()
    const id = await submit(server, `${D01_TEXT} password=hunter2`)
    expect(server.getDetail(id).text).toContain('password=****')
    expect(server.getDetail(id).text).not.toContain('hunter2')
    expect(maskSecrets('token: abc123 키는 AKIAABCDEFGHIJKLMNOP')).toBe('token: **** 키는 ****')
  })
})

describe('재요청', () => {
  it('parent_request_id로 새 요청을 만들고 승인 권고 결과를 돌려준다', async () => {
    const server = newServer()
    const first = await submit(server, D01_TEXT)
    const second = await submit(server, '등록된 파트너 IP로 11/3 화요일 오후 2시에 7일만 열어주세요', first)

    expect(second).not.toBe(first)
    const detail = server.getDetail(second)
    expect(detail.parent_request_id).toBe(first)
    expect(detail.recommendation?.verdict).toBe('승인')
    expect(detail.structured).toMatchObject({ source: '203.0.113.10/32', expires_days: 7 })
    // 1차 요청은 그대로 남아 있다
    expect(server.getDetail(first).parent_request_id).toBeNull()
    expect(server.getDetail(first).recommendation?.verdict).toBe('반려')
  })

  it('없는 parent_request_id는 404다', () => {
    const server = newServer()
    expect(() => server.createRequest(requester, { text: D01_TEXT, parent_request_id: 'REQ-9999' })).toThrow(HttpError)
  })
})

describe('결정과 목록', () => {
  it('승인자가 반려하면 상태가 바뀌고 done 이벤트가 붙는다', async () => {
    const server = newServer()
    const id = await submit(server, D01_TEXT)

    const res = server.decide(id, approver, { decision: 'reject', comment: '정산 기간이라 반려합니다.' })

    expect(res).toMatchObject({ status: 'rejected', approval: { approver: '박CAB', comment: '정산 기간이라 반려합니다.' } })
    expect(server.getDetail(id).status).toBe('rejected')
    expect(server.getDetail(id).nodes.at(-1)).toMatchObject({ node: 'finalize', status: 'done' })
    expect(eventNames(server, id).at(-1)).toBe('done')
  })

  it('요청자는 결정할 수 없고(403) 승인 대기가 아니면 409다', async () => {
    const server = newServer()
    const id = await submit(server, D01_TEXT)

    expect(() => server.decide(id, requester, { decision: 'approve', comment: '' })).toThrowError(/권한/)
    server.decide(id, approver, { decision: 'approve', comment: '' })
    expect(() => server.decide(id, approver, { decision: 'approve', comment: '' })).toThrow(HttpError)
    expect(() => server.decide('REQ-9999', approver, { decision: 'approve', comment: '' })).toThrow(HttpError)
  })

  it('결재함은 위험도 높은 순, 같은 위험도에서는 유사 장애 있음이 먼저다', () => {
    const server = newServer()
    const items = server.listApprovals(approver)

    const ranks = { 높음: 0, 중간: 1, 낮음: 2 } as const
    const keyed = items.map((i) => [i.risk ? ranks[i.risk] : 3, i.has_similar_incident ? 0 : 1])
    expect(keyed).toEqual([...keyed].sort((a, b) => a[0] - b[0] || a[1] - b[1]))
    expect(items.every((i) => i.status === 'awaiting_approval')).toBe(true)
    expect(items[0]).toMatchObject({ risk: '높음', has_similar_incident: true })
  })

  it('자동 승인 기록은 status 쿼리로만 나온다', () => {
    const server = newServer()
    const auto = server.listApprovals(approver, 'auto_approved')

    expect(auto.length).toBeGreaterThan(0)
    expect(auto.every((i) => i.status === 'auto_approved' && i.decided_at !== null && i.standard_change_id !== null)).toBe(true)
    expect(server.listApprovals(approver).some((i) => i.status === 'auto_approved')).toBe(false)
  })

  it('결재함은 승인자만 볼 수 있다', () => {
    expect(() => newServer().listApprovals(requester)).toThrow(HttpError)
  })

  it('내 요청 목록은 요청자 본인 것만, 승인자는 전부 최신순으로 받는다', async () => {
    const server = newServer()
    await submit(server, D01_TEXT)

    const mine = server.listRequests(requester)
    const all = server.listRequests(approver)

    expect(mine.length).toBeGreaterThan(0)
    expect(mine.every((i) => i.requester === '김운영')).toBe(true)
    expect(all.length).toBeGreaterThan(mine.length)
    const times = all.map((i) => i.submitted_at)
    expect(times).toEqual([...times].sort().reverse())
    expect(mine[0]).toMatchObject({ target: 'payments-db', change_type: 'firewall', verdict: '반려' })
  })
})

describe('스트림 복구용 종료 이벤트', () => {
  it('이벤트 기록이 없는 시드 요청은 현재 종료 이벤트 하나를 만든다', () => {
    const server = newServer()
    const [pending] = server.listApprovals(approver)
    const [auto] = server.listApprovals(approver, 'auto_approved')

    expect(server.eventsOf(pending.request_id)).toEqual([])
    expect(server.terminalEvent(pending.request_id)?.event).toBe('awaiting_approval')
    expect(server.terminalEvent(auto.request_id)).toMatchObject({ event: 'done', data: { status: 'auto_approved' } })
  })
})

import { describe, expect, it } from 'vitest'
import type { RequestDetail, SseEventMap, SseEventName } from '../src/api/types'
import { RequestFlow, type Connection, type EventSourceLike } from '../src/lib/flow'
import type { ReviewDetail } from '../src/lib/review'

const AT = '2026-10-29T16:00:00+09:00'

function detail(partial: Partial<RequestDetail> = {}): RequestDetail {
  return {
    request_id: 'REQ-1',
    parent_request_id: null,
    status: 'reviewing',
    version: 'v2',
    requester: '김운영',
    submitted_at: AT,
    text: '본문',
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
    ...partial,
  }
}

/** 브라우저의 EventSource를 흉내 낸다. 시험이 직접 이벤트와 오류를 밀어 넣는다. */
class FakeSource implements EventSourceLike {
  listeners = new Map<string, Array<(event: { data?: unknown }) => void>>()
  closed = false
  addEventListener(name: string, listener: (event: { data?: unknown }) => void) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), listener])
  }
  close() {
    this.closed = true
  }
  push<K extends SseEventName>(name: K, data: SseEventMap[K]) {
    for (const l of this.listeners.get(name) ?? []) l({ data: JSON.stringify(data) })
  }
  /** 연결 오류: 'error' 이름의 이벤트인데 data가 없다 */
  drop() {
    for (const l of this.listeners.get('error') ?? []) l({})
  }
}

function setup(responses: RequestDetail[]) {
  const sources: FakeSource[] = []
  const details: ReviewDetail[] = []
  const connections: Connection[] = []
  const errors: unknown[] = []
  let call = 0
  const flow = new RequestFlow(
    {
      getRequest: async () => responses[Math.min(call++, responses.length - 1)],
      streamUrl: (id) => `/api/requests/${id}/stream`,
      createEventSource: () => {
        const source = new FakeSource()
        sources.push(source)
        return source
      },
      delay: async () => undefined,
    },
    { onDetail: (d) => details.push(d), onConnection: (c) => connections.push(c), onError: (e) => errors.push(e) },
  )
  return { flow, sources, details, connections, errors, calls: () => call }
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

describe('요청 흐름', () => {
  it('검토 중이면 스트림을 열고 이벤트를 상태에 접는다', async () => {
    const { flow, sources, details } = setup([detail()])
    await flow.start('REQ-1')

    expect(sources).toHaveLength(1)
    sources[0].push('node_started', { node: 'parse_request', at: AT })
    sources[0].push('node_completed', { node: 'parse_request', at: AT, error: null })

    expect(details.at(-1)?.nodes).toMatchObject([{ node: 'parse_request', status: 'done' }])
  })

  it('이미 승인 대기인 요청은 스트림을 열지 않는다', async () => {
    const { flow, sources } = setup([detail({ status: 'awaiting_approval' })])
    await flow.start('REQ-1')
    expect(sources).toHaveLength(0)
  })

  it('종료 이벤트를 받으면 스트림을 닫고 GET으로 상태를 맞춘다', async () => {
    const final = detail({ status: 'awaiting_approval', nodes: [] })
    const { flow, sources, details, connections } = setup([detail(), final])
    await flow.start('REQ-1')

    sources[0].push('awaiting_approval', { request_id: 'REQ-1', status: 'awaiting_approval', at: AT })
    await flush()

    expect(sources[0].closed).toBe(true)
    expect(connections.at(-1)).toBe('idle')
    expect(details.at(-1)?.status).toBe('awaiting_approval')
  })

  it('수동 검토 안내 문장은 GET으로 맞춘 뒤에도 남는다', async () => {
    const { flow, sources, details } = setup([detail(), detail({ status: 'manual_review' })])
    await flow.start('REQ-1')

    sources[0].push('done', { request_id: 'REQ-1', status: 'manual_review', at: AT, approval: null, message: '수동 검토로 넘깁니다.' })
    await flush()

    expect(details.at(-1)).toMatchObject({ status: 'manual_review', message: '수동 검토로 넘깁니다.' })
  })

  it('서버가 보낸 error 이벤트는 연결 오류와 구분해 수동 검토로 보고 재연결하지 않는다', async () => {
    const { flow, sources, details } = setup([detail(), detail({ status: 'manual_review' })])
    await flow.start('REQ-1')

    sources[0].push('error', { code: 'internal', message: '자동 검토를 완료하지 못해 수동 검토로 넘깁니다.' })
    await flush()

    expect(details.at(-1)?.status).toBe('manual_review')
    expect(sources).toHaveLength(1)
  })

  it('연결이 끊기면 GET으로 복구하고 아직 검토 중이면 다시 연결한다', async () => {
    const { flow, sources, connections, calls } = setup([detail(), detail({ nodes: [{ node: 'parse_request', status: 'done', started_at: AT, ended_at: AT, error: null }] })])
    await flow.start('REQ-1')

    sources[0].drop()
    await flush()

    expect(sources[0].closed).toBe(true)
    expect(calls()).toBe(2)
    expect(connections).toContain('recovering')
    expect(sources).toHaveLength(2)
    expect(connections.at(-1)).toBe('live')
  })

  it('끊긴 사이에 검토가 끝났으면 다시 연결하지 않는다', async () => {
    const { flow, sources, details, connections } = setup([detail(), detail({ status: 'awaiting_approval' })])
    await flow.start('REQ-1')

    sources[0].drop()
    await flush()

    expect(sources).toHaveLength(1)
    expect(details.at(-1)?.status).toBe('awaiting_approval')
    expect(connections.at(-1)).toBe('idle')
  })

  it('복구 중 상태를 계속 못 읽으면 횟수를 채운 뒤 오류로 멈춘다', async () => {
    const sources: FakeSource[] = []
    const errors: unknown[] = []
    let call = 0
    const flow = new RequestFlow(
      {
        getRequest: async () => {
          call += 1
          if (call === 1) return detail()
          throw new Error('network')
        },
        streamUrl: () => '/s',
        createEventSource: () => {
          const source = new FakeSource()
          sources.push(source)
          return source
        },
        delay: async () => undefined,
      },
      { onDetail: () => undefined, onError: (e) => errors.push(e) },
    )
    await flow.start('REQ-1')

    sources[0].drop()
    await flush()

    expect(errors).toHaveLength(1)
    expect(call).toBe(1 + 5)
  })

  it('다른 요청으로 바꾸면 이전 요청의 늦은 이벤트는 버린다', async () => {
    const { flow, sources, details } = setup([detail({ request_id: 'REQ-1' }), detail({ request_id: 'REQ-2' })])
    await flow.start('REQ-1')
    const old = sources[0]
    await flow.start('REQ-2')

    old.push('node_started', { node: 'parse_request', at: AT })

    expect(old.closed).toBe(true)
    expect(details.at(-1)?.request_id).toBe('REQ-2')
    expect(details.at(-1)?.nodes).toEqual([])
  })

  it('stop하면 스트림을 닫고 idle로 돌린다', async () => {
    const { flow, sources, connections } = setup([detail()])
    await flow.start('REQ-1')
    flow.stop()
    expect(sources[0].closed).toBe(true)
    expect(connections.at(-1)).toBe('idle')
  })
})

import { describe, expect, it, vi } from 'vitest'
import { ApiError, createClient, encodeUserName, roleHeaders } from '../src/api/client'

const requester = { role: 'requester', name: '김운영' } as const

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('역할 헤더', () => {
  it('한글 이름은 퍼센트 인코딩해서 보낸다', () => {
    expect(encodeUserName('김운영')).toBe('%EA%B9%80%EC%9A%B4%EC%98%81')
    expect(decodeURIComponent(encodeUserName('박CAB'))).toBe('박CAB')
  })

  it('X-User-Role과 X-User-Name을 만든다', () => {
    expect(roleHeaders('approver', '박CAB')).toEqual({
      'X-User-Role': 'approver',
      'X-User-Name': encodeURIComponent('박CAB'),
    })
  })

  it('헤더 값은 ISO-8859-1 안의 문자만 쓴다 (fetch가 거부하지 않는다)', () => {
    for (const value of Object.values(roleHeaders('requester', '김운영'))) {
      expect([...value].every((ch) => ch.charCodeAt(0) < 256)).toBe(true)
    }
  })
})

describe('API 클라이언트', () => {
  it('요청마다 역할 헤더를 붙이고 본문이 있으면 JSON으로 보낸다', async () => {
    const fetchFn = vi.fn(async () => jsonResponse({ request_id: 'REQ-1', parent_request_id: null, status: 'reviewing' }, 202))
    const api = createClient({ getUser: () => requester, fetchFn: fetchFn as unknown as typeof fetch })

    const res = await api.createRequest({ text: '방화벽 열어주세요' })

    expect(res.request_id).toBe('REQ-1')
    const [path, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/api/requests')
    expect(init.method).toBe('POST')
    expect(init.headers).toMatchObject({
      'X-User-Role': 'requester',
      'X-User-Name': encodeUserName('김운영'),
      'Content-Type': 'application/json',
    })
    expect(JSON.parse(init.body as string)).toEqual({ text: '방화벽 열어주세요' })
  })

  it('재요청은 parent_request_id를 그대로 보낸다', async () => {
    const fetchFn = vi.fn(async () => jsonResponse({ request_id: 'REQ-2', parent_request_id: 'REQ-1', status: 'reviewing' }, 202))
    const api = createClient({ getUser: () => requester, fetchFn: fetchFn as unknown as typeof fetch })

    await api.createRequest({ text: '고쳐서 다시', parent_request_id: 'REQ-1' })

    const init = (fetchFn.mock.calls[0] as unknown as [string, RequestInit])[1]
    expect(JSON.parse(init.body as string)).toEqual({ text: '고쳐서 다시', parent_request_id: 'REQ-1' })
  })

  it('결재함은 status 쿼리를 붙이고 요청 번호는 경로에 맞게 인코딩한다', async () => {
    const fetchFn = vi.fn(async () => jsonResponse({ items: [] }))
    const api = createClient({ getUser: () => ({ role: 'approver', name: '박CAB' }), fetchFn: fetchFn as unknown as typeof fetch })

    await api.listApprovals('auto_approved')
    await api.listApprovals()
    await api.getRequest('REQ 1/2')

    const paths = fetchFn.mock.calls.map((call) => (call as unknown as [string])[0])
    expect(paths).toEqual(['/api/approvals?status=auto_approved', '/api/approvals', '/api/requests/REQ%201%2F2'])
  })

  it('서버 오류 본문의 code와 message를 ApiError로 돌려준다', async () => {
    const fetchFn = vi.fn(async () => jsonResponse({ error: { code: 'forbidden', message: '이 작업을 할 수 있는 권한이 없어요.' } }, 403))
    const api = createClient({ getUser: () => requester, fetchFn: fetchFn as unknown as typeof fetch })

    const error = await api.decide('REQ-1', { decision: 'approve', comment: '' }).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 403, code: 'forbidden', message: '이 작업을 할 수 있는 권한이 없어요.' })
  })

  it('본문이 JSON이 아닌 5xx는 수동 검토 안내 문장으로 바꾼다', async () => {
    const fetchFn = vi.fn(async () => new Response('<html>boom</html>', { status: 502 }))
    const api = createClient({ getUser: () => requester, fetchFn: fetchFn as unknown as typeof fetch })

    const error = (await api.listRequests().catch((e: unknown) => e)) as ApiError

    expect(error.code).toBe('internal')
    expect(error.message).toBe('자동 검토를 완료하지 못해 수동 검토로 넘깁니다.')
  })

  it('네트워크 오류는 network 코드로 돌려준다', async () => {
    const fetchFn = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    const api = createClient({ getUser: () => requester, fetchFn: fetchFn as unknown as typeof fetch })

    const error = (await api.getRequest('REQ-1').catch((e: unknown) => e)) as ApiError

    expect(error.code).toBe('network')
    expect(error.status).toBe(0)
  })
})

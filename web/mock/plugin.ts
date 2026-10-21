import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Plugin } from 'vite'
import type { ApprovalListStatus, Role } from '../src/api/types.ts'
import { HttpError, MockServer, type DemoUser, type StoredEvent } from './engine.ts'

// VITE_API_MOCK=1일 때만 vite.config.ts가 켜는 개발 서버 미들웨어. /api를 docs/API_CONTRACT.md대로 흉내 낸다.
// VITE_MOCK_DROP=1이면 요청마다 첫 스트림 연결을 세 번째 이벤트 뒤에 끊어서 화면의 복구 경로를 확인한다.
// VITE_MOCK_SPEED는 지연 배율이다(0.2면 5배 빠르게).

export interface MockApiOptions {
  drop?: boolean
  speed?: number
}

function sendJson(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' })
  res.end(JSON.stringify(body))
}

function sendError(res: ServerResponse, error: HttpError): void {
  sendJson(res, error.status, { error: { code: error.code, message: error.message } })
}

function readBody(req: IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = []
    req.on('data', (chunk: Buffer) => chunks.push(chunk))
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf-8')
      if (!raw) return resolve({})
      try {
        resolve(JSON.parse(raw))
      } catch {
        reject(new HttpError(422, 'validation_error', '요청 본문이 올바른 JSON이 아니에요.'))
      }
    })
    req.on('error', reject)
  })
}

/** 헤더에서 역할과 이름을 읽는다. 이름은 퍼센트 인코딩이다(계약 공통). 없거나 잘못되면 403. */
function userFrom(req: IncomingMessage): DemoUser {
  const role = req.headers['x-user-role']
  const rawName = req.headers['x-user-name']
  if ((role !== 'requester' && role !== 'approver') || typeof rawName !== 'string' || !rawName) {
    throw new HttpError(403, 'forbidden', '이 작업을 할 수 있는 권한이 없어요.')
  }
  let name: string
  try {
    name = decodeURIComponent(rawName)
  } catch {
    throw new HttpError(403, 'forbidden', '이 작업을 할 수 있는 권한이 없어요.')
  }
  return { role: role as Role, name }
}

function writeEvent(res: ServerResponse, stored: StoredEvent): void {
  res.write(`event: ${stored.event}\ndata: ${JSON.stringify(stored.data)}\n\n`)
}

/** 종료 이벤트를 보냈으면 스트림을 닫는다(계약: awaiting_approval, done, error 뒤에 닫는다). */
const isEnd = (stored: StoredEvent) =>
  stored.event === 'awaiting_approval' || stored.event === 'done' || stored.event === 'error'

function handleStream(server: MockServer, id: string, req: IncomingMessage, res: ServerResponse, dropOnce: () => boolean): void {
  if (!server.hasRequest(id)) throw new HttpError(404, 'not_found', '요청을 찾을 수 없어요.')

  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  })

  let written = 0
  let closed = false
  const dropAfterThree = dropOnce()
  let unsubscribe: () => void = () => undefined
  const ping = setInterval(() => res.write(': ping\n\n'), 15000)

  const close = () => {
    if (closed) return
    closed = true
    clearInterval(ping)
    unsubscribe()
    res.end()
  }
  const send = (stored: StoredEvent): boolean => {
    if (closed) return false
    writeEvent(res, stored)
    written += 1
    if (dropAfterThree && written === 3) {
      // 연결이 갑자기 끊기는 상황을 흉내 낸다. 마무리 없이 소켓을 닫는다.
      closed = true
      clearInterval(ping)
      unsubscribe()
      res.socket?.destroy()
      return false
    }
    return true
  }
  req.on('close', () => {
    closed = true
    clearInterval(ping)
    unsubscribe()
  })

  // 연결하면 지금까지의 이벤트를 순서대로 다시 보낸다(replay). 기록이 없으면(재시작 뒤와 같음) 종료 이벤트 하나만 보낸다.
  const past = server.eventsOf(id)
  if (past.length === 0) {
    const terminal = server.terminalEvent(id)
    if (terminal) {
      send(terminal)
      close()
      return
    }
  }
  for (let i = 0; i < past.length; i += 1) {
    const stored = past[i]
    if (!send(stored)) return
    const last = i === past.length - 1
    // 승인 대기 뒤에 결정이 이미 났다면(기록이 더 있다면) done까지 보내고 닫는다.
    if (stored.event === 'done' || stored.event === 'error' || (stored.event === 'awaiting_approval' && last)) {
      close()
      return
    }
  }
  unsubscribe = server.subscribe(id, (stored) => {
    if (!send(stored)) return
    if (isEnd(stored)) close()
  })
}

async function route(server: MockServer, req: IncomingMessage, res: ServerResponse, dropOnce: (id: string) => boolean): Promise<void> {
  const url = new URL(req.url ?? '/', 'http://mock.local')
  const method = req.method ?? 'GET'
  const parts = url.pathname.split('/').filter(Boolean) // 'api'는 미들웨어가 이미 뗐다

  if (parts[0] === 'approvals' && parts.length === 1 && method === 'GET') {
    const status = (url.searchParams.get('status') ?? 'awaiting_approval') as ApprovalListStatus
    if (status !== 'awaiting_approval' && status !== 'auto_approved') {
      throw new HttpError(422, 'validation_error', 'status는 awaiting_approval 또는 auto_approved여야 해요.')
    }
    return sendJson(res, 200, { items: server.listApprovals(userFrom(req), status) })
  }

  if (parts[0] === 'requests') {
    if (parts.length === 1 && method === 'POST') {
      const body = (await readBody(req)) as { text?: string; parent_request_id?: string }
      return sendJson(res, 202, server.createRequest(userFrom(req), { text: body.text ?? '', parent_request_id: body.parent_request_id }))
    }
    if (parts.length === 1 && method === 'GET') {
      return sendJson(res, 200, { items: server.listRequests(userFrom(req)) })
    }
    const id = decodeURIComponent(parts[1] ?? '')
    if (parts.length === 2 && method === 'GET') {
      userFrom(req)
      return sendJson(res, 200, server.getDetail(id))
    }
    if (parts.length === 3 && parts[2] === 'stream' && method === 'GET') {
      return handleStream(server, id, req, res, () => dropOnce(id))
    }
    if (parts.length === 3 && parts[2] === 'decision' && method === 'POST') {
      const body = (await readBody(req)) as { decision?: 'approve' | 'reject'; comment?: string }
      return sendJson(res, 200, server.decide(id, userFrom(req), { decision: body.decision as 'approve' | 'reject', comment: body.comment ?? '' }))
    }
  }

  throw new HttpError(404, 'not_found', '요청한 주소를 찾을 수 없어요.')
}

export function mockApi(options: MockApiOptions = {}): Plugin {
  const server = new MockServer({ speed: options.speed })
  // 요청마다 첫 연결에서만 끊는다. 두 번째 연결(복구 뒤 재연결)은 정상으로 이어 받는다.
  const dropped = new Set<string>()
  const dropOnce = (id: string): boolean => {
    if (!options.drop || dropped.has(id)) return false
    dropped.add(id)
    return true
  }

  return {
    name: 'gatekeeper-mock-api',
    configureServer(vite) {
      vite.middlewares.use('/api', (req, res) => {
        route(server, req, res, dropOnce).catch((error: unknown) => {
          if (res.headersSent) return
          if (error instanceof HttpError) return sendError(res, error)
          sendError(res, new HttpError(500, 'internal', '자동 검토를 완료하지 못해 수동 검토로 넘깁니다.'))
        })
      })
    },
  }
}

import { currentUser } from '@/state/session'
import type {
  ApiErrorBody,
  ApiErrorCode,
  ApprovalListStatus,
  CreateRequestBody,
  CreateRequestResponse,
  DecisionBody,
  DecisionResponse,
  RequestDetail,
  RequestListResponse,
  Role,
} from './types'

const SERVER_ERROR_MESSAGE = '자동 검토를 완료하지 못해 수동 검토로 넘깁니다.'
const GENERIC_ERROR_MESSAGE = '요청을 처리하지 못했어요. 잠시 뒤에 다시 시도해 주세요.'
const NETWORK_ERROR_MESSAGE = '서버에 연결하지 못했어요. 연결을 확인하고 다시 시도해 주세요.'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: ApiErrorCode | 'network',
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** HTTP 헤더에는 한글을 그대로 못 넣어서 퍼센트 인코딩한다. 서버가 unquote로 푼다. */
export function encodeUserName(name: string): string {
  return encodeURIComponent(name)
}

export function roleHeaders(role: Role, name: string): Record<string, string> {
  return { 'X-User-Role': role, 'X-User-Name': encodeUserName(name) }
}

export interface ClientDeps {
  getUser: () => { role: Role; name: string }
  /** 테스트에서 바꿔 끼운다. 기본은 전역 fetch. */
  fetchFn?: typeof fetch
}

async function readError(res: Response): Promise<ApiError> {
  try {
    const data = (await res.json()) as Partial<ApiErrorBody>
    if (data.error?.code && data.error.message) {
      return new ApiError(res.status, data.error.code, data.error.message)
    }
  } catch {
    // 본문이 JSON이 아니면 아래 기본 문장을 쓴다.
  }
  const message = res.status >= 500 ? SERVER_ERROR_MESSAGE : GENERIC_ERROR_MESSAGE
  return new ApiError(res.status, 'internal', message)
}

export function createClient({ getUser, fetchFn }: ClientDeps) {
  async function call<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
    const user = getUser()
    const headers: Record<string, string> = { Accept: 'application/json', ...roleHeaders(user.role, user.name) }
    if (body !== undefined) headers['Content-Type'] = 'application/json'

    let res: Response
    try {
      res = await (fetchFn ?? fetch)(path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      })
    } catch {
      throw new ApiError(0, 'network', NETWORK_ERROR_MESSAGE)
    }
    if (!res.ok) throw await readError(res)
    return (await res.json()) as T
  }

  const requestPath = (id: string) => `/api/requests/${encodeURIComponent(id)}`

  return {
    /** 요청 접수. 재요청이면 parent_request_id를 함께 보내고 새 request_id를 받는다. */
    createRequest: (body: CreateRequestBody) => call<CreateRequestResponse>('POST', '/api/requests', body),
    /** 내 요청 목록. 요청자는 본인 것만, 승인자는 전부(서버가 가른다). */
    listRequests: () => call<RequestListResponse>('GET', '/api/requests'),
    getRequest: (id: string) => call<RequestDetail>('GET', requestPath(id)),
    /** 결재함. 기본은 승인 대기, auto_approved면 자동 승인 기록. */
    listApprovals: (status?: ApprovalListStatus) =>
      call<RequestListResponse>('GET', status ? `/api/approvals?status=${status}` : '/api/approvals'),
    decide: (id: string, body: DecisionBody) => call<DecisionResponse>('POST', `${requestPath(id)}/decision`, body),
    /** SSE 주소. EventSource가 헤더를 못 붙이므로 역할 확인 없이 읽기 전용이다. */
    streamUrl: (id: string) => `${requestPath(id)}/stream`,
  }
}

export const api = createClient({ getUser: () => currentUser.value })

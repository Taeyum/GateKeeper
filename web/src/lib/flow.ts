import { SSE_EVENT_NAMES, type RequestDetail, type SseEventMap, type SseEventName } from '@/api/types'
import { applyEvent, type ReviewDetail } from './review'

// 요청 하나의 진행을 화면에 이어 주는 흐름이다: 상태를 읽고, 검토 중이면 SSE를 구독하고,
// 연결이 끊기면 GET으로 상태를 복구한 뒤 다시 잇는다(docs/API_CONTRACT.md 4장 연결 규칙).
// 화면 프레임워크와 브라우저 API에 기대지 않게 필요한 것을 주입받아 단위 테스트로 확인한다.

export interface EventSourceLike {
  addEventListener(name: string, listener: (event: { data?: unknown }) => void): void
  close(): void
}

export interface FlowDeps {
  getRequest: (id: string) => Promise<RequestDetail>
  streamUrl: (id: string) => string
  createEventSource: (url: string) => EventSourceLike
  /** 재연결 전 기다림. 테스트에서 바꿔 끼운다. */
  delay?: (ms: number) => Promise<void>
}

export type Connection = 'idle' | 'live' | 'recovering'

export interface FlowHandlers {
  onDetail: (detail: ReviewDetail) => void
  onConnection?: (state: Connection) => void
  /** 상태를 끝내 읽지 못했을 때 */
  onError?: (error: unknown) => void
}

const END_EVENTS: SseEventName[] = ['awaiting_approval', 'done', 'error']
const MAX_RECONNECTS = 5
const RECONNECT_DELAY_MS = 1000

export class RequestFlow {
  private detail: ReviewDetail | null = null
  private source: EventSourceLike | null = null
  private runId = 0
  private reconnects = 0

  constructor(
    private readonly deps: FlowDeps,
    private readonly handlers: FlowHandlers,
  ) {}

  /** 요청 하나를 따라가기 시작한다. 이미 따라가던 요청이 있으면 멈추고 바꾼다. */
  async start(id: string): Promise<void> {
    this.stop()
    const run = ++this.runId
    this.reconnects = 0
    try {
      const detail = await this.deps.getRequest(id)
      if (run !== this.runId) return
      this.setDetail(detail)
      if (detail.status === 'reviewing') this.connect(id, run)
    } catch (error) {
      if (run === this.runId) this.handlers.onError?.(error)
    }
  }

  stop(): void {
    this.runId += 1
    this.closeSource()
    this.handlers.onConnection?.('idle')
  }

  private setDetail(detail: ReviewDetail): void {
    this.detail = detail
    this.handlers.onDetail(detail)
  }

  private closeSource(): void {
    this.source?.close()
    this.source = null
  }

  private connect(id: string, run: number): void {
    const source = this.deps.createEventSource(this.deps.streamUrl(id))
    this.source = source
    this.handlers.onConnection?.('live')

    for (const name of SSE_EVENT_NAMES) {
      source.addEventListener(name, (event) => {
        if (run !== this.runId || this.source !== source) return
        // 'error'는 서버가 보낸 이벤트(data가 있음)와 연결 오류(data가 없음)가 같은 이름이다.
        if (name === 'error' && typeof event.data !== 'string') {
          void this.recover(id, run, source)
          return
        }
        this.onServerEvent(id, run, name, event.data)
      })
    }
  }

  private onServerEvent(id: string, run: number, name: SseEventName, raw: unknown): void {
    if (!this.detail || typeof raw !== 'string') return
    this.reconnects = 0 // 이벤트가 오고 있으니 연결은 건강하다. 끊김 횟수를 다시 센다.
    let data: SseEventMap[SseEventName]
    try {
      data = JSON.parse(raw) as SseEventMap[SseEventName]
    } catch {
      return // 깨진 이벤트는 건너뛴다. 상태는 종료 때 GET으로 맞춘다.
    }
    this.setDetail(applyEvent(this.detail, name, data))

    if (END_EVENTS.includes(name)) {
      // 종료 이벤트 뒤에는 서버가 스트림을 닫는다. 자동 재연결을 막으려고 먼저 닫고, 최종 상태를 GET으로 맞춘다.
      this.closeSource()
      this.handlers.onConnection?.('idle')
      void this.sync(id, run)
    }
  }

  /** 종료 뒤 상세를 서버 기준으로 맞춘다. 이벤트에 없는 값(시각, 승인 정보)이 여기서 채워진다. */
  private async sync(id: string, run: number): Promise<void> {
    try {
      const detail = await this.deps.getRequest(id)
      if (run !== this.runId) return
      // 수동 검토 안내 문장은 이벤트로만 오므로 이어 붙인다.
      this.setDetail({ ...detail, message: this.detail?.message ?? null })
    } catch {
      // 이벤트로 이미 최종 상태를 알고 있으므로 조용히 둔다.
    }
  }

  /** 스트림이 끊겼다: 상태를 읽어 아직 검토 중이면 다시 잇는다. */
  private async recover(id: string, run: number, broken: EventSourceLike): Promise<void> {
    if (this.source === broken) this.closeSource()
    this.handlers.onConnection?.('recovering')
    const delay = this.deps.delay ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)))

    while (run === this.runId && this.reconnects < MAX_RECONNECTS) {
      this.reconnects += 1
      try {
        const detail = await this.deps.getRequest(id)
        if (run !== this.runId) return
        this.setDetail({ ...detail, message: this.detail?.message ?? null })
        if (detail.status !== 'reviewing') {
          this.handlers.onConnection?.('idle')
          return
        }
        await delay(RECONNECT_DELAY_MS)
        if (run !== this.runId) return
        this.connect(id, run)
        return
      } catch {
        await delay(RECONNECT_DELAY_MS)
      }
    }
    if (run === this.runId) {
      this.handlers.onConnection?.('idle')
      this.handlers.onError?.(new Error('검토 진행 상황을 다시 읽지 못했어요.'))
    }
  }
}

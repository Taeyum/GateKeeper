import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import type {
  ChangeRequest,
  ChangeType,
  HistoryItem,
  Impact,
  Risk,
  ScheduledChange,
  Tier,
  Verdict,
} from '../src/api/types.ts'

// 목업 데이터는 gatekeeper_data의 dev 요청(eval/requests_dev.json)과 data/ 아래 파일 4개만 읽는다.
// eval 폴더의 다른 파일(평가용 정답)은 열지 않는다.
const ROOT = fileURLToPath(new URL('../../gatekeeper_data/', import.meta.url))
const DEV_REQUESTS_FILE = 'eval/requests_dev.json'

function readJson<T>(relativePath: string): T {
  return JSON.parse(readFileSync(ROOT + relativePath, 'utf-8')) as T
}

export interface DevRequest {
  request_id: string
  split: 'dev'
  submitted_at: string
  requester: string
  text: string
  expected: {
    structured: ChangeRequest & { acceptable_change_types?: ChangeType[]; time_note?: string | null }
    violations: string[]
    verdict: Verdict
    emergency_valid: boolean | null
    post_report_required: boolean
    risk: Risk | null
    similar_history: string[] | null
    conditional_alternative: string | null
    rationale: string
  }
}

interface CmdbService {
  id: string
  name: string
  type: string
  tier: Tier
  owner_team: string
  depends_on: string[]
  dependents: string[]
}

interface Incident {
  ticket: string
  status: 'open' | 'closed'
  services: string[]
}

const cmdb = readJson<{ services: CmdbService[] }>('data/cmdb.json')
const calendar = readJson<{ scheduled_changes: ScheduledChange[] }>('data/change_calendar.json')
const incidents = readJson<{ incidents: Incident[] }>('data/open_incidents.json')
const history = readJson<HistoryItem[]>('data/history.json')
const devRequests = readJson<DevRequest[]>(DEV_REQUESTS_FILE)

export function listDevRequests(): DevRequest[] {
  return devRequests
}

export function getDevRequest(id: string): DevRequest | undefined {
  return devRequests.find((r) => r.request_id === id)
}

export function getHistoryItem(id: string): HistoryItem | undefined {
  return history.find((h) => h.history_id === id)
}

/**
 * "유사 장애 있음"의 정의(docs/API_CONTRACT.md): 같은 서비스 + 같은 유형 + outcome이 장애인 이력이 있다.
 * 검색 결과에 장애 항목이 보이는 것과는 별개로 이 정의로 계산한다.
 */
export function hasSimilarIncident(target: string, changeType: ChangeType): boolean {
  return history.some((h) => h.service === target && h.change_type === changeType && h.outcome === '장애')
}

function overlaps(aStart: string, aEnd: string, bStart: string, bEnd: string): boolean {
  return new Date(aStart) < new Date(bEnd) && new Date(bStart) < new Date(aEnd)
}

/** 같은 대상에 작업 구간이 겹치는 예정 변경 (change_calendar.json) */
export function sameTimeChanges(target: string, start: string | null, end: string | null): ScheduledChange[] {
  if (!start || !end) return []
  return calendar.scheduled_changes.filter((c) => c.target === target && overlaps(start, end, c.start, c.end))
}

/** impact_agent 결과를 cmdb.json과 change_calendar.json으로 만든다. 서비스가 없으면 null. */
export function impactFor(target: string, start: string | null, end: string | null): Impact | null {
  const service = cmdb.services.find((s) => s.id === target)
  if (!service) return null
  return {
    id: service.id,
    name: service.name,
    tier: service.tier,
    owner_team: service.owner_team,
    depends_on: service.depends_on,
    dependents: service.dependents,
    same_time_changes: sameTimeChanges(target, start, end),
  }
}

/** open_incidents.json으로 본 긴급 변경 유효성: 티켓이 열려 있고 대상이 장애 범위에 있다. */
export function emergencyValid(ticket: string | null, target: string): boolean {
  if (!ticket) return false
  const incident = incidents.incidents.find((i) => i.ticket === ticket)
  return incident?.status === 'open' && incident.services.includes(target)
}

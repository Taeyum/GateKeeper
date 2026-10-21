import { describe, expect, it } from 'vitest'
import { formatMonth, formatRange, formatSubmitted, formatWhen } from '../src/lib/format'
import { requestTitle, statusBadge, verdictBadge } from '../src/lib/presentation'

describe('시각 표기 (항상 한국 시간)', () => {
  it('정각이면 분을 생략한다', () => {
    expect(formatWhen('2026-10-30T20:00:00+09:00')).toBe('10/30(금) 오후 8시')
    expect(formatWhen('2026-11-03T14:00:00+09:00')).toBe('11/3(화) 오후 2시')
  })

  it('정각이 아니면 분까지 보여 준다', () => {
    expect(formatWhen('2026-11-04T09:30:00+09:00')).toBe('11/4(수) 오전 9:30')
  })

  it('접수 시각은 분까지 항상 보여 준다', () => {
    expect(formatSubmitted('2026-10-29T16:02:00+09:00')).toBe('10/29(목) 오후 4:02')
    expect(formatSubmitted('2026-10-29T16:00:00+09:00')).toBe('10/29(목) 오후 4:00')
  })

  it('다른 시간대의 시각도 한국 시간으로 바꿔 보여 준다', () => {
    // 2026-10-30T11:00:00Z = 한국 20:00
    expect(formatWhen('2026-10-30T11:00:00Z')).toBe('10/30(금) 오후 8시')
  })

  it('같은 날 구간은 끝에 시각만 붙인다', () => {
    expect(formatRange('2026-10-30T20:00:00+09:00', '2026-10-30T21:00:00+09:00')).toBe('10/30(금) 오후 8시 ~ 9시')
    expect(formatRange('2026-10-30T11:00:00+09:00', '2026-10-30T13:00:00+09:00')).toBe('10/30(금) 오전 11시 ~ 오후 1시')
    expect(formatRange('2026-10-30T20:00:00+09:00', null)).toBe('10/30(금) 오후 8시')
  })

  it('날이 다르면 양쪽을 모두 보여 준다', () => {
    expect(formatRange('2026-10-30T23:00:00+09:00', '2026-10-31T01:00:00+09:00')).toBe('10/30(금) 오후 11시 ~ 10/31(토) 오전 1시')
  })

  it('이력 날짜는 연월만 보여 준다', () => {
    expect(formatMonth('2025-11-14')).toBe('2025년 11월')
  })
})

describe('요청 이름과 배지', () => {
  it('요청 번호 대신 대상과 유형으로 이름을 만든다', () => {
    expect(requestTitle('payments-db', 'firewall')).toBe('payments-db 방화벽 열기')
    expect(requestTitle('order-service', 'deploy')).toBe('order-service 배포')
    expect(requestTitle('billing-db', 'db_change')).toBe('billing-db DB 변경')
    expect(requestTitle('auth-service', 'standard', 'STD-01')).toBe('auth-service 로그 레벨 변경')
    expect(requestTitle('web-02', 'standard', 'STD-02')).toBe('web-02 인증서 갱신')
    expect(requestTitle(null, null)).toBe('분석 중인 요청')
  })

  it('판정과 상태 배지는 글자를 항상 가진다', () => {
    expect(verdictBadge('반려')).toMatchObject({ label: '반려 권고', tone: 'reject' })
    expect(verdictBadge('자동 승인')).toMatchObject({ label: '자동 승인', tone: 'auto' })
    expect(statusBadge('awaiting_approval')).toMatchObject({ label: '승인자 확인 중', tone: 'wait' })
    expect(statusBadge('manual_review').label).toBe('수동 검토')
  })
})

import { describe, expect, it } from 'vitest'
import { GROUPS, GROUP_ORDER, RULE_COPY, groupViolations } from '../src/copy/rules'
import type { AlternativeDiffItem, Violation } from '../src/api/types'

const violation = (rule_id: string, severity: Violation['severity'] = 'forbidden'): Violation => ({
  rule_id,
  severity,
  evidence: `엔진 문장 ${rule_id}`,
})

describe('규정 문구 매핑 표', () => {
  it('위반이 될 수 있는 CHG-01~10 중 CHG-07을 뺀 9개를 모두 덮는다', () => {
    for (const id of ['CHG-01', 'CHG-02', 'CHG-03', 'CHG-04', 'CHG-05', 'CHG-06', 'CHG-08', 'CHG-09', 'CHG-10']) {
      expect(RULE_COPY[id], id).toBeDefined()
      expect(RULE_COPY[id].plain.length).toBeGreaterThan(0)
    }
    // CHG-07은 위반이 아니라 긴급 변경 절차 안내다
    expect(RULE_COPY['CHG-07']).toBeUndefined()
  })

  it('쉬운 문구에는 가운뎃점이 없고 규정 번호도 들어 있지 않다', () => {
    for (const copy of Object.values(RULE_COPY)) {
      expect(copy.plain).not.toContain(String.fromCharCode(0xb7))
      expect(copy.plain).not.toMatch(/CHG-\d+/)
    }
  })

  it('CHG-05는 요청 시점 규정으로, CHG-03은 외부 노출 금지로 설명한다', () => {
    expect(RULE_COPY['CHG-05'].plain).toBe('핵심 서비스는 작업 24시간 전까지 요청해야 해요.')
    expect(RULE_COPY['CHG-03'].plain).toBe('DB 포트는 외부에 열 수 없어요. 등록된 파트너 IP 1개(/32)만 예외예요.')
  })

  it('모든 묶음은 제목을 가지고 보이는 순서에 들어 있다', () => {
    expect(GROUP_ORDER).toHaveLength(Object.keys(GROUPS).length)
    for (const key of GROUP_ORDER) {
      expect(GROUPS[key].title.length).toBeGreaterThan(0)
      expect(GROUPS[key].problem.length).toBeGreaterThan(0)
    }
  })
})

describe('위반 묶기', () => {
  it('REQ-D01의 위반 5개를 시간 / 접속할 곳 / 기간 3묶음으로 나눈다', () => {
    const { groups } = groupViolations(
      ['CHG-01', 'CHG-02', 'CHG-03', 'CHG-09', 'CHG-10'].map((id) => violation(id, id === 'CHG-09' ? 'fixable' : 'forbidden')),
      null,
    )

    expect(groups.map((g) => [g.title, g.items.map((i) => i.rule_id)])).toEqual([
      ['작업 시간을 바꿔 주세요', ['CHG-01', 'CHG-10']],
      ['접속할 곳을 정해 주세요', ['CHG-02', 'CHG-03']],
      ['열어 둘 기간을 적어 주세요', ['CHG-09']],
    ])
    // 승인자 화면은 같은 묶음을 상황 제목으로 보여 준다
    expect(groups.map((g) => g.problem)).toEqual(['작업 시간이 규정에 맞지 않아요', '접속 범위가 너무 넓어요', '열어 둘 기간이 없어요'])
  })

  it('쉬운 문구와 엔진 문장을 둘 다 가진다', () => {
    const [group] = groupViolations([violation('CHG-01')], null).groups
    expect(group.items[0]).toMatchObject({ plain: RULE_COPY['CHG-01'].plain, evidence: '엔진 문장 CHG-01' })
  })

  it('모르는 rule_id는 엔진 문장으로 대신하고 "그 밖의 확인할 것"에 넣는다', () => {
    const { groups } = groupViolations([violation('CHG-99')], null)
    expect(groups).toHaveLength(1)
    expect(groups[0].title).toBe('그 밖의 확인할 것')
    expect(groups[0].items[0].plain).toBe('엔진 문장 CHG-99')
  })

  it('묶음은 보이는 순서(시간, 접속, 기간, 롤백, 테스트, 공지)를 따른다', () => {
    const { groups } = groupViolations(['CHG-05', 'CHG-08', 'CHG-04', 'CHG-09'].map((id) => violation(id, 'fixable')), null)
    expect(groups.map((g) => g.key)).toEqual(['period', 'rollback', 'test', 'notice'])
  })

  it('alternative_diff는 field가 맞는 묶음에 붙는다', () => {
    const diff: AlternativeDiffItem[] = [
      { field: 'start', label: '작업 시간', current: '2026-10-30T20:00:00+09:00', suggested: '2026-11-03T14:00:00+09:00' },
      { field: 'source', label: '접속할 곳', current: null, suggested: '등록된 파트너 IP /32' },
      { field: 'expires_days', label: '열어 둘 기간', current: null, suggested: 7 },
    ]
    const { groups, extraDiff } = groupViolations(
      ['CHG-01', 'CHG-02', 'CHG-09'].map((id) => violation(id)),
      diff,
    )

    expect(groups.map((g) => g.diff.map((d) => d.field))).toEqual([['start'], ['source'], ['expires_days']])
    expect(extraDiff).toEqual([])
  })

  it('해당 묶음이 없거나 모르는 field의 비교는 extraDiff로 남는다', () => {
    const diff: AlternativeDiffItem[] = [
      { field: 'source', label: '접속할 곳', current: null, suggested: '등록된 파트너 IP /32' },
      { field: 'port', label: '포트', current: 5432, suggested: 5433 },
    ]
    const { groups, extraDiff } = groupViolations([violation('CHG-01')], diff)

    expect(groups[0].diff).toEqual([])
    expect(extraDiff.map((d) => d.field)).toEqual(['source', 'port'])
  })

  it('위반이 없으면 묶음도 없다', () => {
    expect(groupViolations([], null)).toEqual({ groups: [], extraDiff: [] })
  })
})

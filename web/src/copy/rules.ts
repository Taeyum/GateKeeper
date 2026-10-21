import type { AlternativeDiffItem, Violation } from '@/api/types'

// 규정 번호(rule_id)를 쉬운 문구와 묶음으로 바꾸는 표다.
// 한계: 문구의 숫자(30일, 24시간)는 화면에 고정된 규정 값이다. 규정(rules.json)이 바뀌면 이 표도 고쳐야 한다.
// 엔진이 만든 근거 문장은 "자세한 근거"에 그대로 보여 주고, 이 표에 없는 rule_id는 엔진 문장으로 대신한다.

export type GroupKey = 'time' | 'access' | 'period' | 'rollback' | 'test' | 'notice' | 'etc'

interface GroupSpec {
  title: string
  /** 이 묶음의 "지금 -> 이렇게" 비교에 쓰는 ChangeRequest 필드 */
  diffFields: string[]
}

export const GROUPS: Record<GroupKey, GroupSpec> = {
  time: { title: '작업 시간을 바꿔 주세요', diffFields: ['start', 'end'] },
  access: { title: '접속할 곳을 정해 주세요', diffFields: ['source'] },
  period: { title: '열어 둘 기간을 적어 주세요', diffFields: ['expires_days', 'expires_at'] },
  rollback: { title: '되돌리는 방법을 적어 주세요', diffFields: ['has_rollback'] },
  test: { title: '테스트 결과를 붙여 주세요', diffFields: ['has_test_evidence'] },
  notice: { title: '더 일찍 요청해 주세요', diffFields: [] },
  etc: { title: '그 밖의 확인할 것', diffFields: [] },
}

/** 화면에 보이는 묶음 순서 */
export const GROUP_ORDER: GroupKey[] = ['time', 'access', 'period', 'rollback', 'test', 'notice', 'etc']

export const RULE_COPY: Record<string, { group: GroupKey; plain: string }> = {
  'CHG-01': { group: 'time', plain: '금요일 저녁부터 월요일 아침까지는 변경할 수 없어요.' },
  'CHG-02': { group: 'access', plain: '접속할 곳(IP)을 정하지 않으면 누구나 접속할 수 있는 상태로 봐요.' },
  'CHG-03': { group: 'access', plain: 'DB 포트는 외부에 열 수 없어요. 등록된 파트너 IP 1개(/32)만 예외예요.' },
  'CHG-04': { group: 'rollback', plain: '문제가 생기면 되돌릴 방법(롤백 계획)을 적어 주세요.' },
  'CHG-05': { group: 'notice', plain: '핵심 서비스는 작업 24시간 전까지 요청해야 해요.' },
  'CHG-06': { group: 'time', plain: '같은 서비스에 같은 시간대 작업이 이미 있어요.' },
  'CHG-08': { group: 'test', plain: '운영에 반영하기 전에 테스트 서버에서 확인한 결과를 붙여 주세요.' },
  'CHG-09': { group: 'period', plain: '외부 접속을 열 때는 기간(최대 30일)을 정해야 해요.' },
  'CHG-10': { group: 'time', plain: '월말 정산이나 대형 행사처럼 변경이 막힌 기간이에요.' },
}

/** CHG-07은 위반이 아니라 긴급 변경의 절차다. 위반 목록이 아니라 안내로 보여 준다. */
export const EMERGENCY_NOTICE = '긴급 변경은 열려 있는 장애 티켓이 필요하고, 24시간 안에 사후 보고를 해야 해요.'

export interface FixItem {
  rule_id: string
  plain: string
  evidence: string
  severity: Violation['severity']
}

export interface FixGroup {
  key: GroupKey
  title: string
  items: FixItem[]
  /** 이 묶음에 붙는 "지금 -> 이렇게" 비교. 없으면 비어 있다. */
  diff: AlternativeDiffItem[]
}

export interface GroupedViolations {
  groups: FixGroup[]
  /** 어느 묶음에도 속하지 않은 비교 항목(알 수 없는 field, 해당 묶음이 없음) */
  extraDiff: AlternativeDiffItem[]
}

/**
 * 위반 목록을 쉬운 묶음으로 나누고, alternative_diff의 항목을 알맞은 묶음에 붙인다.
 * 비교 항목은 field가 맞는 첫 묶음(보이는 순서 기준)에만 붙는다.
 */
export function groupViolations(
  violations: Violation[],
  diff: AlternativeDiffItem[] | null | undefined,
): GroupedViolations {
  const byGroup = new Map<GroupKey, FixItem[]>()
  for (const violation of violations) {
    const copy = RULE_COPY[violation.rule_id]
    const key: GroupKey = copy?.group ?? 'etc'
    const item: FixItem = {
      rule_id: violation.rule_id,
      plain: copy?.plain ?? violation.evidence,
      evidence: violation.evidence,
      severity: violation.severity,
    }
    byGroup.set(key, [...(byGroup.get(key) ?? []), item])
  }

  const groups: FixGroup[] = GROUP_ORDER.filter((key) => byGroup.has(key)).map((key) => ({
    key,
    title: GROUPS[key].title,
    items: byGroup.get(key) ?? [],
    diff: [],
  }))

  const extraDiff: AlternativeDiffItem[] = []
  for (const item of diff ?? []) {
    const target = groups.find((group) => GROUPS[group.key].diffFields.includes(item.field))
    if (target) target.diff.push(item)
    else extraDiff.push(item)
  }
  return { groups, extraDiff }
}

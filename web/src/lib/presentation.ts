import type { IconName } from '@/components/icons'
import type { ChangeType, RequestStatus, Risk, StandardId, Verdict } from '@/api/types'

export type Tone = 'reject' | 'warn' | 'ok' | 'auto' | 'wait'

export interface BadgeSpec {
  label: string
  tone: Tone
  icon: IconName
}

/** 시스템 권고(verdict)의 배지. 색만으로 구분하지 않도록 글자와 아이콘을 함께 쓴다. */
export function verdictBadge(verdict: Verdict): BadgeSpec {
  switch (verdict) {
    case '반려':
      return { label: '반려 권고', tone: 'reject', icon: 'x-circle' }
    case '조건부 승인':
      return { label: '조건부 승인', tone: 'warn', icon: 'alert' }
    case '승인':
      return { label: '승인', tone: 'ok', icon: 'check-circle' }
    case '자동 승인':
      return { label: '자동 승인', tone: 'auto', icon: 'bolt' }
  }
}

/** 처리 상태(status)의 배지. 권고와는 별개다. */
export function statusBadge(status: RequestStatus): BadgeSpec {
  switch (status) {
    case 'reviewing':
      return { label: '확인 중', tone: 'wait', icon: 'spinner' }
    case 'awaiting_approval':
      return { label: '승인자 확인 중', tone: 'wait', icon: 'clock' }
    case 'approved':
      return { label: '승인됨', tone: 'ok', icon: 'check-circle' }
    case 'rejected':
      return { label: '반려됨', tone: 'reject', icon: 'x-circle' }
    case 'auto_approved':
      return { label: '자동 승인', tone: 'auto', icon: 'bolt' }
    case 'manual_review':
      return { label: '수동 검토', tone: 'warn', icon: 'alert' }
  }
}

/**
 * 결과 요약의 상태 칩을 보여 줄지. 자동 승인은 판정 배지(자동 승인)와 상태 칩(자동 승인)이 같은 말이라
 * 위아래로 두 번 나오지 않게 칩을 숨긴다. 그 밖에는 권고(승인 권고)와 처리 상태(승인자 확인 중, 승인됨)가 다른 정보다.
 */
export function showStatusChip(verdict: Verdict, status: RequestStatus): boolean {
  return !(verdict === '자동 승인' && status === 'auto_approved')
}

/** 위험도 막대: 채워지는 칸 수 */
export const RISK_LEVEL: Record<Risk, 1 | 2 | 3> = { 낮음: 1, 중간: 2, 높음: 3 }

export const CHANGE_TYPE_LABEL: Record<ChangeType, string> = {
  firewall: '방화벽',
  deploy: '배포',
  db_change: 'DB 변경',
  standard: '표준 변경',
}

export const STANDARD_LABEL: Record<StandardId, string> = {
  'STD-01': '로그 레벨 변경',
  'STD-02': '인증서 갱신',
}

/** 요청 번호 대신 대상과 유형으로 만든 요청 이름. 예: "payments-db 방화벽 열기" */
export function requestTitle(
  target: string | null,
  changeType: ChangeType | null,
  standardId: StandardId | null = null,
): string {
  if (!target || !changeType) return '분석 중인 요청'
  switch (changeType) {
    case 'firewall':
      return `${target} 방화벽 열기`
    case 'deploy':
      return `${target} 배포`
    case 'db_change':
      return `${target} DB 변경`
    case 'standard':
      return `${target} ${standardId ? STANDARD_LABEL[standardId] : '표준 변경'}`
  }
}

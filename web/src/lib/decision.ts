import type { Verdict } from '@/api/types'

export type Decision = 'approve' | 'reject'

/**
 * 시스템 권고와 다르게 결정할 때만 사유를 꼭 적게 한다(서버는 강제하지 않고 화면이 안내한다).
 * - 반려 권고인데 승인한다
 * - 승인 또는 자동 승인 권고인데 반려한다
 * 조건부 승인 권고는 승인도 반려도 권고와 어긋나지 않는다(조건을 보고 사람이 정한다).
 */
export function needsReason(verdict: Verdict | null, decision: Decision): boolean {
  if (verdict === '반려') return decision === 'approve'
  if (verdict === '승인' || verdict === '자동 승인') return decision === 'reject'
  return false
}

/** 권고와 다르게 결정하는지 (사유 안내 문구를 고르는 데 쓴다) */
export function reasonHint(verdict: Verdict | null, decision: Decision): string | null {
  if (!needsReason(verdict, decision)) return null
  return decision === 'approve'
    ? '시스템은 반려를 권고했어요. 승인하려면 이유를 적어 주세요.'
    : '시스템은 승인을 권고했어요. 반려하려면 이유를 적어 주세요.'
}

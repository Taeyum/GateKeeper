import { describe, expect, it } from 'vitest'
import { needsReason, reasonHint } from '../src/lib/decision'

describe('권고와 다른 결정의 사유', () => {
  it('반려 권고를 승인할 때만 사유가 필요하다', () => {
    expect(needsReason('반려', 'approve')).toBe(true)
    expect(needsReason('반려', 'reject')).toBe(false)
  })

  it('승인과 자동 승인 권고를 반려할 때만 사유가 필요하다', () => {
    expect(needsReason('승인', 'reject')).toBe(true)
    expect(needsReason('승인', 'approve')).toBe(false)
    expect(needsReason('자동 승인', 'reject')).toBe(true)
  })

  it('조건부 승인 권고는 어느 쪽이든 사유를 강제하지 않는다', () => {
    expect(needsReason('조건부 승인', 'approve')).toBe(false)
    expect(needsReason('조건부 승인', 'reject')).toBe(false)
  })

  it('권고가 없으면 강제하지 않는다', () => {
    expect(needsReason(null, 'approve')).toBe(false)
  })

  it('다르게 결정할 때만 안내 문구를 돌려준다', () => {
    expect(reasonHint('반려', 'approve')).toBe('시스템은 반려를 권고했어요. 승인하려면 이유를 적어 주세요.')
    expect(reasonHint('승인', 'reject')).toBe('시스템은 승인을 권고했어요. 반려하려면 이유를 적어 주세요.')
    expect(reasonHint('반려', 'reject')).toBeNull()
  })
})

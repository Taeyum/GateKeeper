import { describe, expect, it } from 'vitest'
import { currentUser, homePathFor, session, setRole, USERS } from '../src/state/session'

describe('역할 전환 상태', () => {
  it('저장소를 못 쓰는 환경에서도 요청자로 시작한다', () => {
    expect(session.role).toBe('requester')
    expect(currentUser.value).toEqual(USERS.requester)
  })

  it('setRole은 저장 실패와 상관없이 역할을 바꾼다', () => {
    setRole('approver')
    expect(currentUser.value.name).toBe('박CAB')
    setRole('requester')
    expect(currentUser.value.name).toBe('김운영')
  })

  it('역할에 맞는 첫 화면 경로를 돌려준다', () => {
    expect(homePathFor('requester')).toBe('/')
    expect(homePathFor('approver')).toBe('/approvals')
  })
})

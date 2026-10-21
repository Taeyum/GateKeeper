import { computed, reactive } from 'vue'
import type { Role } from '@/api/types'

// 데모용 역할 전환. 실서비스는 SSO가 사용자를 정한다(docs/API_CONTRACT.md 공통).
export interface DemoUser {
  role: Role
  name: string
  /** 역할 전환에 보이는 이름 */
  label: string
}

export const USERS: Record<Role, DemoUser> = {
  requester: { role: 'requester', name: '김운영', label: '요청자 김운영' },
  approver: { role: 'approver', name: '박CAB', label: '승인자 박CAB' },
}

const STORAGE_KEY = 'gk.role'

function loadRole(): Role {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'approver' ? 'approver' : 'requester'
  } catch {
    // 저장소를 못 쓰는 환경(시크릿 창, 테스트)에서는 요청자로 시작한다.
    return 'requester'
  }
}

export const session = reactive<{ role: Role }>({ role: loadRole() })

export const currentUser = computed<DemoUser>(() => USERS[session.role])

export function setRole(role: Role): void {
  session.role = role
  try {
    localStorage.setItem(STORAGE_KEY, role)
  } catch {
    // 저장은 편의 기능이라 실패해도 화면은 그대로 동작한다.
  }
}

/** 역할에 맞는 첫 화면 경로 */
export function homePathFor(role: Role): string {
  return role === 'approver' ? '/approvals' : '/'
}

<script setup lang="ts">
import { useRouter } from 'vue-router'
import type { Role } from '@/api/types'
import { homePathFor, session, setRole, USERS } from '@/state/session'
import AppIcon from './AppIcon.vue'

// 모든 화면 위쪽의 남색 밴드: 로고, 메뉴, 역할 전환, 큰 제목.
// 메뉴는 기본으로 요청자 메뉴(요청 작성/내 요청)를 그리고, 승인자 화면은 nav 슬롯으로 탭을 넣는다.
defineProps<{
  title: string
  subtitle?: string
  /** 요청자 메뉴에서 눌린 항목 */
  active?: 'compose' | 'mine'
}>()

const router = useRouter()
const roles: Role[] = ['requester', 'approver']

async function switchRole(role: Role) {
  if (role === session.role) return
  setRole(role)
  await router.push(homePathFor(role))
}
</script>

<template>
  <header class="band">
    <div class="inner">
      <div class="top">
        <div class="left">
          <RouterLink class="logo" :to="homePathFor(session.role)">
            <AppIcon name="shield" :size="24" :stroke-width="2" />
            GateKeeper
          </RouterLink>
          <!-- 승인자는 화면이 넘긴 탭(nav 슬롯)을, 요청자는 요청 메뉴를 보여 준다. 역할이 맞지 않는 화면에서도 메뉴가 어긋나지 않는다. -->
          <slot v-if="session.role === 'approver'" name="nav" />
          <nav v-else class="pills" aria-label="주 메뉴">
            <RouterLink class="pill" :class="{ on: active === 'compose' }" :aria-current="active === 'compose' ? 'page' : undefined" to="/">요청 작성</RouterLink>
            <RouterLink class="pill" :class="{ on: active === 'mine' }" :aria-current="active === 'mine' ? 'page' : undefined" :to="{ path: '/', hash: '#my-requests' }">내 요청</RouterLink>
          </nav>
        </div>
        <div class="roles" role="group" aria-label="역할 전환 (데모)">
          <button
            v-for="role in roles"
            :key="role"
            type="button"
            class="role"
            :class="{ on: session.role === role }"
            :aria-pressed="session.role === role"
            @click="switchRole(role)"
          >
            {{ USERS[role].label }}
          </button>
        </div>
      </div>

      <div class="titlebar">
        <div>
          <div v-if="$slots.breadcrumb" class="crumb"><slot name="breadcrumb" /></div>
          <h1>{{ title }}</h1>
          <p v-if="subtitle" class="sub">{{ subtitle }}</p>
        </div>
        <div v-if="$slots.actions" class="actions"><slot name="actions" /></div>
      </div>
    </div>
  </header>
</template>

<style scoped>
.band {
  background: var(--band);
  color: #fff;
  border-radius: 0 0 28px 28px;
  padding: 14px var(--gutter) 36px;
}
.inner {
  max-width: calc(var(--content-max) - var(--gutter) * 2);
  margin: 0 auto;
}
.top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}
.left {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 28px;
}
.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #fff;
  font-size: 20px;
  font-weight: 700;
  letter-spacing: -0.01em;
}
.pills {
  display: flex;
  gap: 6px;
}
.pill {
  display: flex;
  align-items: center;
  height: 40px;
  padding: 0 18px;
  border: 1px solid transparent;
  border-radius: var(--radius-pill);
  color: rgba(255, 255, 255, 0.82);
  font-size: 15px;
  font-weight: 500;
}
.pill.on {
  background: rgba(255, 255, 255, 0.14);
  border-color: rgba(255, 255, 255, 0.22);
  color: #fff;
  font-weight: 600;
}
.roles {
  display: flex;
  gap: 2px;
  padding: 3px;
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: var(--radius-pill);
}
.role {
  display: flex;
  align-items: center;
  height: 34px;
  padding: 0 14px;
  border: 0;
  border-radius: var(--radius-pill);
  background: transparent;
  color: rgba(255, 255, 255, 0.86);
  font-size: 14px;
  font-weight: 500;
}
.role.on {
  background: #fff;
  color: var(--ink);
  font-weight: 600;
}
.titlebar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 32px;
}
.crumb {
  margin-bottom: 6px;
  color: rgba(255, 255, 255, 0.78);
  font-size: 14px;
}
h1 {
  font-size: 34px;
  font-weight: 500;
  letter-spacing: -0.02em;
  line-height: 1.25;
}
.sub {
  max-width: 720px;
  margin-top: 10px;
  color: rgba(255, 255, 255, 0.82);
  font-size: 16px;
}
.actions {
  color: rgba(255, 255, 255, 0.82);
  font-size: 14px;
}
</style>

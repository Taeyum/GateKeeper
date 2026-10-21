<script setup lang="ts">
import { useRouter } from 'vue-router'
import type { Role } from '@/api/types'
import { homePathFor, session, setRole, USERS } from '@/state/session'
import NoticeBox from './NoticeBox.vue'

// 화면마다 맞는 역할이 있다. 다른 역할로 열면 화면을 보여 주는 대신 전환 버튼이 있는 안내를 보여 준다.
const props = defineProps<{ role: Role; what: string }>()
const router = useRouter()

async function switchToRole() {
  setRole(props.role)
  await router.push(homePathFor(props.role))
}
</script>

<template>
  <slot v-if="session.role === role" />
  <div v-else class="gate">
    <NoticeBox>
      {{ what }}은 {{ USERS[role].label }} 역할로 볼 수 있어요.
      <button type="button" class="switch" @click="switchToRole">{{ USERS[role].label }}(으)로 전환</button>
    </NoticeBox>
  </div>
</template>

<style scoped>
.gate {
  max-width: 720px;
}
.switch {
  margin-left: 6px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--blue);
  font-weight: 600;
  text-decoration: underline;
  text-underline-offset: 3px;
}
</style>

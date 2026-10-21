<script setup lang="ts">
import { computed } from 'vue'
import type { Approval, NodeRun, RequestStatus } from '@/api/types'
import AppIcon from './AppIcon.vue'
import type { IconName } from './icons'

// "지금 어디까지 왔나요?": 자동 검토가 끝났고, 사람 결정이 어디까지 왔는지 세 줄로 알린다.
const props = defineProps<{ status: RequestStatus; nodes: NodeRun[]; approval: Approval | null }>()

interface Step {
  title: string
  body: string
  icon: IconName
  done: boolean
}

const steps = computed<Step[]>(() => {
  const ran = (name: string) => props.nodes.some((n) => n.node === name)
  const list: Step[] = [
    {
      title: '자동 검토 완료',
      body: ran('impact_agent') ? '규정, 서비스 영향, 과거 사고를 확인했어요.' : '규정에 어긋나는 곳이 있는지 확인했어요.',
      icon: 'check-circle',
      done: true,
    },
  ]
  if (ran('validate')) {
    list.push({ title: '내용 점검 완료', body: '설명이 규정과 맞는지 한 번 더 확인했어요.', icon: 'check-circle', done: true })
  }
  switch (props.status) {
    case 'awaiting_approval':
      list.push({ title: '승인자 확인 중', body: '변경관리자가 결정하면 여기에서 알려 드려요.', icon: 'clock', done: false })
      break
    case 'approved':
      list.push({ title: '승인됨', body: `${props.approval?.approver ?? '변경관리자'}가 승인했어요.`, icon: 'check-circle', done: true })
      break
    case 'rejected':
      list.push({ title: '반려됨', body: `${props.approval?.approver ?? '변경관리자'}가 반려했어요.`, icon: 'x-circle', done: true })
      break
    case 'auto_approved':
      list.push({ title: '자동 승인', body: '승인자 확인 없이 승인 처리됐어요.', icon: 'bolt', done: true })
      break
    default:
      break
  }
  return list
})
</script>

<template>
  <section class="card box" aria-labelledby="process-title">
    <h2 id="process-title">지금 어디까지 왔나요?</h2>
    <ol>
      <li v-for="step in steps" :key="step.title">
        <AppIcon :name="step.icon" :size="22" :class="{ pending: !step.done }" />
        <div>
          <div class="title">{{ step.title }}</div>
          <div class="body">{{ step.body }}</div>
        </div>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.box {
  padding: 28px;
}
h2 {
  margin-bottom: 16px;
  font-size: 18px;
  font-weight: 600;
}
ol {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
li {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
li :deep(svg) {
  margin-top: 1px;
  color: var(--ink);
}
li :deep(svg.pending) {
  color: var(--wait-fg);
}
.title {
  font-weight: 600;
}
.body {
  color: var(--ink2);
  font-size: 14.5px;
}
</style>

<script setup lang="ts">
import { computed } from 'vue'
import type { ChangeRequest } from '@/api/types'
import { understoodRows } from '@/lib/review'
import AppIcon from './AppIcon.vue'

// "이렇게 이해했어요": 요청 문장을 항목으로 나눈 결과. 꼭 필요한데 빠진 항목에는 "빠졌어요"를 붙인다.
const props = defineProps<{ structured: ChangeRequest; serviceName?: string | null }>()
const rows = computed(() => understoodRows(props.structured, props.serviceName))
const hasMissing = computed(() => rows.value.some((r) => r.missing))
</script>

<template>
  <section class="card box" aria-labelledby="understood-title">
    <h2 id="understood-title">이렇게 이해했어요</h2>
    <p class="lead">
      {{ hasMissing ? '빠진 내용은 결과 화면에서 어떻게 채우면 되는지 알려 드려요.' : '필요한 내용이 모두 있어요.' }}
    </p>
    <dl>
      <div v-for="row in rows" :key="row.label" class="row">
        <dt>{{ row.label }}</dt>
        <dd>
          <span class="value" :class="{ muted: row.missing }">{{ row.value }}</span>
          <span v-if="row.missing" class="missing"><AppIcon name="alert" :size="13" :stroke-width="2.4" />빠졌어요</span>
          <span v-if="row.note" class="note">{{ row.note }}</span>
        </dd>
      </div>
    </dl>
  </section>
</template>

<style scoped>
.box {
  padding: 32px;
}
h2 {
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.01em;
}
.lead {
  margin: 4px 0 14px;
  color: var(--ink2);
}
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 0;
  border-top: 1px solid var(--line);
}
dt {
  color: var(--ink2);
}
dd {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 4px 10px;
  text-align: right;
}
.value {
  font-weight: 600;
}
.value.muted {
  color: var(--ink2);
  font-weight: 400;
}
.missing {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 10px;
  border: 1px solid var(--warn-line);
  border-radius: var(--radius-pill);
  background: var(--warn-bg);
  color: var(--warn-fg);
  font-size: 13px;
  font-weight: 600;
}
.note {
  flex-basis: 100%;
  color: var(--ink3);
  font-size: 13.5px;
  font-weight: 400;
}
</style>

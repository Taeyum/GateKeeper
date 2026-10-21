<script setup lang="ts">
import type { Recommendation } from '@/api/types'
import { RULE_COPY } from '@/copy/rules'

// 자세한 근거(접힘): 시스템 설명과 걸린 규정마다 규정 번호, 이름, 엔진이 만든 근거 문장을 그대로 보여 준다.
defineProps<{ recommendation: Recommendation; label?: string }>()

const SEVERITY_TEXT = { forbidden: '금지', fixable: '보완' } as const
</script>

<template>
  <details class="details">
    <summary>
      {{ label ?? (recommendation.violations.length ? `자세한 근거 보기 (걸린 규정 ${recommendation.violations.length}개)` : '자세한 설명 보기') }}
    </summary>
    <p v-if="recommendation.explanation" class="explain">{{ recommendation.explanation }}</p>
    <ul class="list">
      <li v-for="violation in recommendation.violations" :key="violation.rule_id" class="item">
        <div class="head">
          <span>
            <span class="code">{{ violation.rule_id }}</span>
            <strong v-if="RULE_COPY[violation.rule_id]" class="name">{{ RULE_COPY[violation.rule_id].plain }}</strong>
          </span>
          <span class="sev" :class="violation.severity">{{ SEVERITY_TEXT[violation.severity] }}</span>
        </div>
        <p class="evidence">{{ violation.evidence }}</p>
      </li>
    </ul>
    <p v-if="!recommendation.violations.length" class="none">걸린 규정이 없어요.</p>
  </details>
</template>

<style scoped>
.details {
  font-size: 15px;
}
summary {
  font-size: 16px;
  font-weight: 600;
}
.explain {
  margin-top: 14px;
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--soft);
  color: var(--ink2);
}
.list {
  margin-top: 8px;
}
.item {
  padding: 14px 0;
  border-top: 1px solid var(--line);
}
.item:first-child {
  margin-top: 8px;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.code {
  padding: 1px 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--bg);
  font-family: var(--mono);
  font-size: 13px;
}
.name {
  margin-left: 8px;
  font-weight: 600;
}
.sev {
  flex: none;
  padding: 2px 10px;
  border-radius: var(--radius-pill);
  font-size: 13px;
  font-weight: 600;
}
.sev.forbidden {
  background: var(--reject-bg);
  color: var(--reject-fg);
}
.sev.fixable {
  background: var(--warn-bg);
  color: var(--warn-fg);
}
.evidence {
  margin-top: 6px;
  color: var(--ink2);
}
.none {
  margin-top: 12px;
  color: var(--ink3);
}
</style>

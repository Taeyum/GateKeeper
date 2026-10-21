<script setup lang="ts">
import { computed } from 'vue'
import type { Risk } from '@/api/types'
import { RISK_LEVEL } from '@/lib/presentation'

// 위험도: 채워진 칸 수와 글자("높음")를 함께 보여 준다. 표준 변경처럼 위험도가 없으면 줄표.
const props = defineProps<{ risk: Risk | null }>()
const level = computed(() => (props.risk ? RISK_LEVEL[props.risk] : 0))
</script>

<template>
  <span v-if="risk" class="risk">
    <span class="bars" aria-hidden="true">
      <i v-for="n in 3" :key="n" :class="{ on: n <= level }" />
    </span>
    {{ risk }}
  </span>
  <span v-else class="none">해당 없음</span>
</template>

<style scoped>
.risk {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  white-space: nowrap;
}
.bars {
  display: inline-flex;
  gap: 2px;
}
.bars i {
  display: block;
  width: 10px;
  height: 6px;
  border-radius: 2px;
  background: var(--line2);
}
.bars i.on {
  background: var(--ink);
}
.none {
  color: var(--ink3);
  white-space: nowrap;
}
</style>

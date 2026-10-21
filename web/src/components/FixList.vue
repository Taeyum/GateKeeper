<script setup lang="ts">
import type { AlternativeDiffItem } from '@/api/types'
import type { FixGroup } from '@/copy/rules'
import { formatDiffValue } from '@/lib/review'
import AppIcon from './AppIcon.vue'

// "이렇게 고쳐 주세요": 위반을 쉬운 묶음으로 나눠 보여 주고, 항목별 대안이 있으면 "지금 -> 이렇게"로 비교한다.
// 규정 번호는 맨 아래 작은 글씨로만 보여 준다. 엔진의 근거 문장은 "자세한 근거"가 맡는다.
defineProps<{
  heading: string
  groups: FixGroup[]
  extraDiff: AlternativeDiffItem[]
}>()
</script>

<template>
  <div>
    <h2>{{ heading }}</h2>
    <p class="lead">아래 내용으로 바꿔서 다시 요청하면 돼요.</p>

    <ol class="list">
      <li v-for="(group, index) in groups" :key="group.key" class="group">
        <span class="num" aria-hidden="true">{{ index + 1 }}</span>
        <div class="main">
          <h3>{{ group.title }}</h3>
          <p v-for="item in group.items" :key="item.rule_id" class="plain">{{ item.plain }}</p>

          <div v-for="item in group.diff" :key="item.field" class="diff">
            <div class="from">
              <div class="tag">지금</div>
              <div>{{ formatDiffValue(item.field, item.current) }}</div>
            </div>
            <AppIcon name="arrow-right" :size="22" class="arrow" />
            <div class="to">
              <div class="tag">이렇게</div>
              <div class="strong">{{ formatDiffValue(item.field, item.suggested) }}</div>
            </div>
          </div>

          <p class="codes">
            관련 규정
            <span v-for="item in group.items" :key="item.rule_id" class="code">{{ item.rule_id }}</span>
          </p>
        </div>
      </li>
    </ol>

    <div v-if="extraDiff.length" class="extra">
      <h3>그 밖에 바꿀 내용</h3>
      <div v-for="item in extraDiff" :key="item.field" class="diff">
        <div class="from">
          <div class="tag">{{ item.label }} / 지금</div>
          <div>{{ formatDiffValue(item.field, item.current) }}</div>
        </div>
        <AppIcon name="arrow-right" :size="22" class="arrow" />
        <div class="to">
          <div class="tag">이렇게</div>
          <div class="strong">{{ formatDiffValue(item.field, item.suggested) }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
h2 {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.01em;
}
.lead {
  margin: 4px 0 24px;
  color: var(--ink2);
}
.list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.group {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 22px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--soft);
}
.num {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--ink);
  color: #fff;
  font-size: 15px;
  font-weight: 700;
}
.main {
  flex: 1;
  min-width: 0;
}
h3 {
  font-size: 18px;
  font-weight: 600;
}
.plain {
  margin-top: 4px;
  color: var(--ink2);
}
.diff {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 28px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  margin-top: 14px;
}
.from,
.to {
  padding: 12px 16px;
  border: 1px solid var(--line2);
  border-radius: var(--radius-sm);
  background: var(--surface);
  font-size: 16px;
}
.from {
  color: var(--ink2);
}
.to {
  border-color: var(--blue-line);
  background: var(--blue-bg);
}
.tag {
  color: var(--ink3);
  font-size: 13px;
}
.to .tag {
  color: var(--blue);
  font-weight: 600;
}
.strong {
  font-weight: 700;
}
.arrow {
  color: var(--ink3);
}
.codes {
  margin-top: 12px;
  color: var(--ink3);
  font-size: 13.5px;
}
.code {
  margin-left: 6px;
  font-family: var(--mono);
}
.extra {
  margin-top: 16px;
}
</style>

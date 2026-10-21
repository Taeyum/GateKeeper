<script setup lang="ts">
import { computed } from 'vue'
import type { Verdict } from '@/api/types'
import { verdictBadge } from '@/lib/presentation'
import Badge from './Badge.vue'

const props = withDefaults(defineProps<{ verdict: Verdict; size?: 'sm' | 'md' | 'lg' }>(), { size: 'md' })
const spec = computed(() => verdictBadge(props.verdict))
</script>

<template>
  <Badge :tone="spec.tone" :icon="spec.icon" :label="spec.label" :size="size" />
</template>

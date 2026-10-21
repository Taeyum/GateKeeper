<script setup lang="ts">
import { computed } from 'vue'
import type { RequestStatus } from '@/api/types'
import { statusBadge } from '@/lib/presentation'
import Badge from './Badge.vue'

const props = withDefaults(defineProps<{ status: RequestStatus; size?: 'sm' | 'md' | 'lg' }>(), { size: 'md' })
const spec = computed(() => statusBadge(props.status))
</script>

<template>
  <Badge :tone="spec.tone" :icon="spec.icon" :label="spec.label" :size="size" />
</template>

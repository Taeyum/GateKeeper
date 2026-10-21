<script setup lang="ts">
import PageBand from '@/components/PageBand.vue'

defineProps<{ id: string }>()
</script>

<template>
  <PageBand title="검토하고 있어요" active="mine" />
  <main class="page" />
</template>

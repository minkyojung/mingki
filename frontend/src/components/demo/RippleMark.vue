<script setup lang="ts">
/** 로고의 '물결'을 옮긴 원형 표식. animate면 바깥 고리가 퍼진다 */
defineProps<{ animate?: boolean }>()
</script>

<template>
  <div class="relative grid size-14 place-items-center">
    <span
      v-if="animate"
      aria-hidden="true"
      class="absolute inset-0 animate-ping rounded-full bg-brand/20 [animation-duration:1.8s]"
    />
    <span aria-hidden="true" class="absolute inset-0 rounded-full bg-brand-soft" />
    <span aria-hidden="true" class="absolute inset-2 rounded-full bg-brand/15" />
    <span class="relative grid size-8 place-items-center rounded-full bg-brand text-white">
      <slot><span class="size-2.5 rounded-full bg-white" /></slot>
    </span>
  </div>
</template>

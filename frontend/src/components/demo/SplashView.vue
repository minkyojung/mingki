<script setup lang="ts">
/** 처음 들어올 때 한 번: 넓은 화면은 왼쪽 그림 · 오른쪽 시작, 좁은 화면은 그림 아래 버튼만 */
import { ArrowRightIcon } from '@lucide/vue'

import splashUrl from '@/assets/splash.webp'
import { Button } from '@/components/ui/button'

const emit = defineEmits<{ enter: [] }>()
</script>

<template>
  <div class="flex h-svh flex-col bg-white lg:grid lg:grid-cols-[1.1fr_1fr]">
    <!-- 그림 (로고가 그림 안에 있다) -->
    <div class="relative min-h-0 flex-1">
      <img
        :src="splashUrl"
        alt="Dumbo"
        width="941"
        height="1334"
        class="splash-in absolute inset-0 size-full object-contain pt-4 select-none lg:py-8"
        draggable="false"
      />
    </div>

    <!-- 시작 -->
    <div
      class="px-6 pt-2 pb-8 lg:flex lg:items-center lg:justify-center lg:border-l lg:border-border/60 lg:bg-background lg:py-16"
    >
      <div class="mx-auto w-full max-w-sm">
        <div class="hidden lg:block">
          <p
            class="splash-in inline-flex rounded-full bg-brand-soft px-3 py-1 text-sm font-medium text-accent-foreground [animation-delay:160ms]"
          >
            규정 변경 영향 분석
          </p>
          <h1 class="splash-in mt-4 text-[2rem] leading-tight font-bold [animation-delay:220ms]">
            규정이 바뀌면,<br />상품 규칙까지 한 번에
          </h1>
          <p class="splash-in mt-4 leading-relaxed text-muted-foreground [animation-delay:280ms]">
            바뀐 조항을 찾고, 영향받는 상품을 확인해<br />상품 규칙에 반영해요.
          </p>
        </div>
        <Button
          size="lg"
          class="splash-in group h-14 w-full rounded-2xl text-base shadow-lg shadow-brand/20 [animation-delay:340ms] lg:mt-12"
          @click="emit('enter')"
        >
          Dumbo로 규제 반영하기
          <ArrowRightIcon class="transition-transform group-hover:translate-x-0.5" />
        </Button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.splash-in {
  animation: splash-in 600ms cubic-bezier(0.2, 0.7, 0.2, 1) both;
}

@keyframes splash-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
}
</style>

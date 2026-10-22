<script setup lang="ts">
/** AI 작업 중 · 반영 중: 덤보가 일하는 모습, 큰 제목 하나와 지금 하는 일 목록 */
import { CheckIcon } from '@lucide/vue'

import applyingGif from '@/assets/mascot/applying.gif'
import readingGif from '@/assets/mascot/reading.gif'
import type { Step } from '@/lib/briefing'

defineProps<{ title: string; seconds: number; steps: Step[]; mode: 'reading' | 'applying' }>()
</script>

<template>
  <section class="stage pt-4">
    <!-- GIF가 스스로 반복된다. 단계가 끝나 화면이 바뀌면 함께 사라진다 -->
    <img
      :key="mode"
      :src="mode === 'applying' ? applyingGif : readingGif"
      alt=""
      width="480"
      height="360"
      class="mx-auto w-full max-w-80 mix-blend-multiply select-none [mask-image:radial-gradient(ellipse_closest-side,#000_72%,transparent)]"
      draggable="false"
    />
    <h1 class="mt-2 text-center text-[1.75rem] leading-tight font-bold">{{ title }}</h1>
    <p class="mt-2 text-center text-muted-foreground tabular-nums">{{ seconds }}초</p>

    <ol
      v-if="steps.length"
      class="mt-8 space-y-5 rounded-2xl bg-card p-6 shadow-xs ring-1 ring-foreground/5"
    >
      <li
        v-for="step in steps"
        :key="step.label"
        class="flex gap-3"
        :class="{
          'font-semibold': step.state === 'active',
          'text-muted-foreground/60': step.state === 'todo',
        }"
      >
        <span
          v-if="step.state === 'done'"
          class="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-brand text-white"
        >
          <CheckIcon class="size-3" stroke-width="3" />
        </span>
        <span
          v-else-if="step.state === 'active'"
          class="relative mt-0.5 grid size-5 shrink-0 place-items-center"
        >
          <span class="absolute size-5 animate-ping rounded-full bg-brand/30" />
          <span class="size-2.5 rounded-full bg-brand" />
        </span>
        <span
          v-else
          class="mt-0.5 size-5 shrink-0 rounded-full border-2 border-dashed border-border"
        />
        <div>
          {{ step.label }}
          <p v-if="step.detail" class="mt-1 text-sm font-normal text-muted-foreground">
            {{ step.detail }}
          </p>
        </div>
      </li>
    </ol>
  </section>
</template>

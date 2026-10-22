<script setup lang="ts">
/** 시연 화면 한 페이지: 시작 → AI 작업 중 → 확인 (⇄ 다시 쓰는 중) → (반영 중) → 완료. 흐름은 useDemo, 화면 계산은 lib/briefing */
import { RotateCcwIcon } from '@lucide/vue'
import { computed, onMounted, watch } from 'vue'

import logoUrl from '@/assets/logo.png'
import DoneView from '@/components/demo/DoneView.vue'
import ReviewView from '@/components/demo/ReviewView.vue'
import RunningView from '@/components/demo/RunningView.vue'
import StartView from '@/components/demo/StartView.vue'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { useDemo } from '@/composables/useDemo'
import { useReviewBoard } from '@/composables/useReviewBoard'
import {
  appliedCount,
  appliedProposals,
  buildAiLog,
  buildBriefing,
  elapsedSeconds,
  progressSteps,
  type Step,
} from '@/lib/briefing'

const {
  phase,
  task,
  events,
  requirements,
  proposals,
  canApprove,
  error,
  busy,
  now,
  revising,
  revisionErrors,
  init,
  start,
  revise,
  approve,
  reset,
  retry,
} = useDemo()

// 확인 화면의 선택(체크 · 바꾸기 확정 · 편집 초안). 다시 쓰는 동안 확인 화면이 사라져도 남는다
const review = useReviewBoard(computed(() => task.value?.task.id))
function restart() {
  review.forget()
  return reset()
}

onMounted(init)

const briefing = computed(() => buildBriefing(requirements.value, proposals.value))
const aiLog = computed(() => buildAiLog(events.value, requirements.value, proposals.value))
const applied = computed(() => buildBriefing(requirements.value, appliedProposals(proposals.value)))
// 다시 쓴 제안의 초안은 쓰였으니 지운다
watch(briefing, (b) => {
  const versions = new Map(b.groups.flatMap((g) => g.rows.map((r) => [r.key, r.version])))
  if (phase.value === 'review' && versions.size) review.prune(versions)
})

// 반영 중 · 다시 쓰는 중에는 확인한 때(RESUMED)부터, 편집한 제안 여러 개면 처음 보낸 때부터 센다
const seconds = computed(() => {
  if (revising.value) return Math.round((now.value - revising.value.startedAt) / 1000)
  const since = phase.value === 'publishing' || phase.value === 'revising' ? 'RESUMED' : undefined
  return elapsedSeconds(events.value, now.value, since)
})

// 확인 화면은 오른쪽 완료 목록까지 넓게, 나머지는 한 줄
const wide = computed(() => (phase.value === 'review' ? 'max-w-5xl' : 'max-w-xl'))

const running = computed((): { title: string; steps: Step[] } => {
  const detail = task.value
  if (!detail) return { title: 'AI가 확인하고 있어요', steps: [] }
  if (phase.value === 'revising') {
    const progress = revising.value
    return {
      title: '편집한 내용으로 다시 만들고 있어요',
      steps: [
        {
          label: progress
            ? `${progress.done} / ${progress.total}개 다시 만들었어요`
            : 'AI가 제안을 다시 쓰고 있어요',
          state: 'active',
        },
      ],
    }
  }
  if (phase.value === 'publishing') {
    const total = proposals.value.filter((p) => p.latest && p.decision === 'APPLY').length
    return {
      title: '상품 규칙에 반영하고 있어요',
      steps: [
        {
          label: `${Math.min(appliedCount(events.value), total)} / ${total}건 반영했어요`,
          state: 'active',
        },
      ],
    }
  }
  return {
    title: 'AI가 확인하고 있어요',
    steps: progressSteps(
      detail.task.status,
      detail.task.verifyRound,
      detail.counts.requirements,
      events.value,
    ),
  }
})
</script>

<template>
  <div class="relative min-h-svh bg-background">
    <!-- 위쪽에만 아주 옅게 번지는 브랜드 색 -->
    <div
      aria-hidden="true"
      class="pointer-events-none absolute inset-x-0 top-0 h-96 bg-[radial-gradient(60%_100%_at_50%_0%,var(--color-brand-soft),transparent)]"
    />

    <header class="sticky top-0 z-10 border-b border-border/60 bg-background/75 backdrop-blur-md">
      <div class="mx-auto flex h-16 items-center px-5" :class="wide">
        <span class="flex items-center">
          <img :src="logoUrl" alt="" class="h-6 w-auto select-none" draggable="false" />
          <span class="sr-only">Dumbo</span>
        </span>
        <Button
          variant="ghost"
          size="sm"
          class="ml-auto rounded-full px-3 text-muted-foreground"
          :disabled="busy"
          @click="restart"
        >
          <RotateCcwIcon />
          초기화
        </Button>
      </div>
    </header>

    <main class="relative mx-auto px-5" :class="[wide, { 'pb-40': phase === 'review' }]">
      <Alert
        v-if="error && phase !== 'failed' && phase !== 'review'"
        variant="destructive"
        class="mt-4"
      >
        <AlertDescription>{{ error }}</AlertDescription>
      </Alert>

      <div v-if="phase === 'loading'" class="stage items-center">
        <Spinner class="size-7 text-brand" />
      </div>
      <StartView v-else-if="phase === 'start'" :busy="busy" @start="start" />
      <RunningView
        v-else-if="phase === 'running' || phase === 'revising' || phase === 'publishing'"
        :title="running.title"
        :seconds="seconds"
        :steps="running.steps"
        :mode="phase === 'running' ? 'reading' : 'applying'"
      />
      <ReviewView
        v-else-if="phase === 'review'"
        :briefing="briefing"
        :log="aiLog"
        :review="review"
        :revision-errors="revisionErrors"
        :can-approve="canApprove"
        :busy="busy"
        :error="error"
        @revise="revise"
        @approve="approve"
      />
      <DoneView
        v-else-if="phase === 'done' && task"
        :applied="applied"
        :status="task.task.status"
        :busy="busy"
        @restart="restart"
      />
      <section v-else-if="phase === 'failed'" class="stage gap-6">
        <Alert variant="destructive">
          <AlertTitle>작업을 이어갈 수 없어요</AlertTitle>
          <AlertDescription>{{ error }}</AlertDescription>
        </Alert>
        <div class="grid grid-cols-2 gap-2">
          <Button
            variant="outline"
            size="lg"
            class="h-12 rounded-xl"
            :disabled="busy"
            @click="retry"
          >
            다시 시도
          </Button>
          <Button size="lg" class="h-12 rounded-xl" :disabled="busy" @click="restart"
            >초기화</Button
          >
        </div>
      </section>
    </main>
  </div>
</template>

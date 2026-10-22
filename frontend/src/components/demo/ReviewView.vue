<script setup lang="ts">
/**
 * 확인: 결론 한 문장 → 바뀐 조항별 카드(체크 · 편집) | 오른쪽 확인 완료 목록 → 아래 버튼.
 * [확인하기] 체크한 제안을 확인 완료 목록으로 옮기고, 편집한 제안은 AI에게 다시 쓰게 한다(수정 요청).
 * 모두 목록에 들어가면 [반영하기] → 바로 반영(승인은 서버에 한 번).
 */
import { CheckIcon, RotateCcwIcon } from '@lucide/vue'
import { computed } from 'vue'

import { demoRegulation } from '@/api/tasks'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { RevisionItem } from '@/composables/useDemo'
import type { ReviewBoard } from '@/composables/useReviewBoard'
import type { AiLog as AiLogData, Briefing } from '@/lib/briefing'
import { formatDate } from '@/lib/briefing'
import { draftOf, revisionComment } from '@/lib/revision'
import AiLog from './AiLog.vue'
import ChangeCard from './ChangeCard.vue'
import NoteList from './NoteList.vue'

const props = defineProps<{
  briefing: Briefing
  log: AiLogData
  review: ReviewBoard
  revisionErrors: Record<string, string>
  canApprove: boolean
  busy: boolean
  error: string | null
}>()
const emit = defineEmits<{ revise: [items: RevisionItem[]]; approve: [excludedKeys: string[]] }>()

const board = computed(() => props.review.board)
const rows = computed(() =>
  props.briefing.groups.flatMap((g) => g.rows.map((row) => ({ ...row, title: g.title }))),
)
// 왼쪽은 아직 확정하지 않은 제안만, 오른쪽은 확정한 제안
const remaining = computed(() =>
  props.briefing.groups
    .map((g) => ({ ...g, rows: g.rows.filter((r) => !board.value.confirmed.has(r.key)) }))
    .filter((g) => g.rows.length),
)
const done = computed(() => rows.value.filter((r) => board.value.confirmed.has(r.key)))
const chosen = computed(() =>
  rows.value.filter((r) => !r.failed && !board.value.excluded.has(r.key)),
)
const pending = computed(() => chosen.value.filter((r) => !board.value.confirmed.has(r.key)))
const drafted = computed(() => pending.value.filter((r) => board.value.drafts[r.key]))
const allDone = computed(() => !pending.value.length && done.value.length > 0)
const effective = computed(() => formatDate(props.briefing.effectiveDate))

/** 고치지 않은 제안은 완료 목록으로, 고친 제안은 수정 요청으로 (같은 제안 묶음은 한 번만) */
function change() {
  props.review.confirm(pending.value.filter((r) => !board.value.drafts[r.key]).map((r) => r.key))
  const items = new Map<string, RevisionItem>()
  for (const row of drafted.value) {
    if (items.has(row.proposalId)) continue
    items.set(row.proposalId, {
      proposalId: row.proposalId,
      comment: revisionComment(draftOf(row), board.value.drafts[row.key]!),
    })
  }
  if (items.size) emit('revise', [...items.values()])
}

/** 확정하지 않은 제안(체크 해제 · 검사 실패)은 빼고 반영한다 */
function finish() {
  emit(
    'approve',
    props.briefing.changeKeys.filter((key) => !board.value.confirmed.has(key)),
  )
}
</script>

<template>
  <section class="pt-14">
    <p
      class="inline-flex items-center rounded-full bg-brand-soft px-3 py-1 text-sm font-medium text-accent-foreground"
    >
      {{ demoRegulation.title }} · {{ effective }} 시행
    </p>
    <h1 class="mt-4 text-[2rem] leading-tight font-bold">
      상품 <span class="text-brand">{{ briefing.productCount }}개</span>를 바꿔야 해요
    </h1>
    <AiLog :log="log" class="mt-4" />

    <div class="mt-8 grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem] lg:items-start">
      <div>
        <div class="space-y-4">
          <ChangeCard
            v-for="group in remaining"
            :key="group.reqKey"
            :group="group"
            :excluded="board.excluded"
            :drafts="board.drafts"
            :errors="revisionErrors"
            :busy="busy"
            @toggle="review.toggle"
            @save="review.saveDraft"
            @discard="review.discardDraft"
            @confirm="review.confirm"
          />
        </div>
        <p
          v-if="!remaining.length"
          class="rounded-2xl bg-card p-6 text-center text-muted-foreground ring-1 ring-foreground/5"
        >
          모든 상품을 확인했어요
        </p>

        <div class="mt-6 text-sm">
          <NoteList
            v-if="briefing.needsCheck.length"
            :title="`확인이 필요한 상품 ${briefing.needsCheck.length}개`"
            :items="briefing.needsCheck"
            warn
          />
          <NoteList
            v-if="briefing.keep.length"
            title="그대로 둬도 되는 상품"
            :items="briefing.keep"
          />
        </div>
      </div>

      <!-- 확인 완료 목록 -->
      <Card class="gap-0 rounded-2xl py-0 ring-foreground/5 lg:sticky lg:top-24">
        <CardHeader class="gap-3 border-b py-5">
          <CardTitle class="flex items-center justify-between">
            확인 완료
            <span class="text-sm font-medium text-muted-foreground tabular-nums">
              {{ done.length }} / {{ chosen.length }}
            </span>
          </CardTitle>
          <div class="h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              class="h-full rounded-full bg-brand transition-[width] duration-500"
              :style="{ width: `${chosen.length ? (done.length / chosen.length) * 100 : 0}%` }"
            />
          </div>
        </CardHeader>
        <CardContent class="px-0">
          <p v-if="!done.length" class="px-6 py-6 text-center text-sm text-muted-foreground">
            확인하기를 누르면 여기에 모여요
          </p>
          <ul v-else class="divide-y">
            <li v-for="row in done" :key="row.key" class="flex items-start gap-3 px-6 py-3.5">
              <span
                class="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-brand text-white"
              >
                <CheckIcon class="size-3" stroke-width="3" />
              </span>
              <div class="min-w-0 flex-1 text-sm">
                <p class="truncate font-medium">{{ row.productName }}</p>
                <p class="truncate text-xs text-muted-foreground">
                  {{ row.title }} · {{ row.change }}
                  <template v-if="row.revisionOutcome === 'revised'"> · AI 수정 반영</template>
                </p>
              </div>
              <Button
                variant="ghost"
                size="icon-xs"
                class="text-muted-foreground"
                :disabled="busy"
                :aria-label="`${row.productName} 되돌리기`"
                @click="review.unconfirm(row.key)"
              >
                <RotateCcwIcon />
              </Button>
            </li>
          </ul>
        </CardContent>
      </Card>
    </div>

    <div
      class="fixed inset-x-0 bottom-0 bg-linear-to-t from-background via-background/95 to-transparent pt-10 pb-6"
    >
      <div class="mx-auto max-w-5xl px-5 lg:grid lg:grid-cols-[minmax(0,1fr)_20rem] lg:gap-6">
        <div>
          <!-- 모두 확인 완료 목록에 들어가면 반영하기 → 반영 중 화면 -->
          <Button
            v-if="allDone"
            size="lg"
            class="h-14 w-full rounded-2xl text-base shadow-lg shadow-brand/20"
            :disabled="!canApprove || busy"
            @click="finish"
          >
            반영하기
          </Button>
          <Button
            v-else
            size="lg"
            class="h-14 w-full rounded-2xl text-base shadow-lg shadow-brand/20"
            :disabled="!pending.length || busy"
            @click="change"
          >
            확인하기
          </Button>

          <p v-if="error" role="alert" class="mt-2 text-center text-sm text-destructive">
            {{ error }}
          </p>
          <p
            v-else-if="allDone && !canApprove"
            class="mt-2 text-center text-sm text-muted-foreground"
          >
            검사를 통과하지 못한 수정안이 있어 반영할 수 없어요
          </p>
          <p v-else-if="drafted.length" class="mt-2 text-center text-sm text-muted-foreground">
            편집한 {{ drafted.length }}개는 AI가 다시 만들어요
          </p>
          <p
            v-else-if="!allDone && !pending.length"
            class="mt-2 text-center text-sm text-muted-foreground"
          >
            바꿀 상품을 하나 이상 골라 주세요
          </p>
        </div>
      </div>
    </div>
  </section>
</template>

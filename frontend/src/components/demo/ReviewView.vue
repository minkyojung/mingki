<script setup lang="ts">
/** 확인: 결론 한 문장 → 바뀐 조항별 카드(체크) → 바꾸지 않는 상품(접힘) → 아래 [바꾸기] → 확인 창 */
import { computed, ref, watch } from 'vue'

import { demoRegulation } from '@/api/tasks'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import type { AiLog as AiLogData, Briefing } from '@/lib/briefing'
import { formatDate } from '@/lib/briefing'
import AiLog from './AiLog.vue'
import ChangeCard from './ChangeCard.vue'
import NoteList from './NoteList.vue'

const props = defineProps<{
  briefing: Briefing
  log: AiLogData
  canApprove: boolean
  busy: boolean
  error: string | null
}>()
const emit = defineEmits<{ approve: [excludedKeys: string[]] }>()

// 처음에는 모든 제안을 반영하도록 체크해 둔다
const selected = ref(new Set<string>())
watch(
  () => props.briefing.changeKeys,
  (keys) => (selected.value = new Set(keys)),
  { immediate: true },
)

function toggle(keys: string[], on: boolean) {
  const next = new Set(selected.value)
  for (const key of keys) {
    if (on) next.add(key)
    else next.delete(key)
  }
  selected.value = next
}

const productIds = computed(
  () =>
    new Set(
      props.briefing.groups.flatMap((g) =>
        g.rows.filter((r) => selected.value.has(r.key)).map((r) => r.productId),
      ),
    ),
)
const effective = computed(() => formatDate(props.briefing.effectiveDate))

function confirm() {
  emit(
    'approve',
    props.briefing.changeKeys.filter((key) => !selected.value.has(key)),
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

    <div class="mt-8 space-y-4">
      <ChangeCard
        v-for="group in briefing.groups"
        :key="group.reqKey"
        :group="group"
        :selected="selected"
        @toggle="toggle"
      />
    </div>

    <div class="mt-6 text-sm">
      <NoteList
        v-if="briefing.needsCheck.length"
        :title="`확인이 필요한 상품 ${briefing.needsCheck.length}개`"
        :items="briefing.needsCheck"
        warn
      />
      <NoteList v-if="briefing.keep.length" title="그대로 둬도 되는 상품" :items="briefing.keep" />
    </div>

    <div
      class="fixed inset-x-0 bottom-0 bg-linear-to-t from-background via-background/95 to-transparent pt-10 pb-6"
    >
      <div class="mx-auto max-w-xl px-5">
        <AlertDialog>
          <AlertDialogTrigger as-child>
            <Button
              size="lg"
              class="h-14 w-full rounded-2xl text-base shadow-lg shadow-brand/20"
              :disabled="!canApprove || !productIds.size || busy"
            >
              바꾸기
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent class="rounded-2xl">
            <AlertDialogHeader>
              <AlertDialogTitle>상품 {{ productIds.size }}개를 바꿀까요?</AlertDialogTitle>
              <AlertDialogDescription>{{ effective }}부터 적용돼요</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>취소</AlertDialogCancel>
              <AlertDialogAction @click="confirm">바꾸기</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
        <p v-if="error" role="alert" class="mt-2 text-center text-sm text-destructive">
          {{ error }}
        </p>
        <p v-else-if="!canApprove" class="mt-2 text-center text-sm text-muted-foreground">
          검사를 통과하지 못한 수정안이 있어 바꿀 수 없어요
        </p>
      </div>
    </div>
  </section>
</template>

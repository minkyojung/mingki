<script setup lang="ts">
/** 완료: 반영한 상품 수 한 문장, 바뀐 내용은 접어 둔다 */
import { CheckIcon, ChevronRightIcon } from '@lucide/vue'
import { computed } from 'vue'

import type { TaskStatus } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import type { Briefing } from '@/lib/briefing'
import { formatDate } from '@/lib/briefing'
import RippleMark from './RippleMark.vue'

const props = defineProps<{ applied: Briefing; status: TaskStatus; busy: boolean }>()
const emit = defineEmits<{ restart: [] }>()

const title = computed(() => {
  if (props.status === 'NO_CHANGE') return '바꿀 상품이 없어요'
  if (props.status === 'REJECTED') return '반영하지 않았어요'
  return `상품 ${props.applied.productCount}개를 바꿨어요`
})
</script>

<template>
  <section class="stage">
    <RippleMark class="self-start">
      <CheckIcon class="size-4" stroke-width="3" />
    </RippleMark>
    <h1 class="mt-8 text-[2rem] leading-tight font-bold">{{ title }}</h1>
    <p v-if="applied.groups.length" class="mt-2 text-muted-foreground">
      {{ formatDate(applied.effectiveDate) }}부터 새 규칙이 적용돼요
    </p>

    <Collapsible v-if="applied.groups.length" class="mt-8 text-sm">
      <CollapsibleTrigger
        class="group inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
      >
        바뀐 내용 보기
        <ChevronRightIcon class="size-4 transition-transform group-data-[state=open]:rotate-90" />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <Card class="mt-3 rounded-2xl py-2 ring-foreground/5">
          <CardContent class="divide-y px-5">
            <p v-for="group in applied.groups" :key="group.reqKey" class="flex gap-4 py-3">
              <span>
                {{ group.title }}
                {{
                  group.added
                    ? '추가'
                    : group.before && group.after
                      ? `${group.before} → ${group.after}`
                      : ''
                }}
              </span>
              <span class="ml-auto font-medium text-brand tabular-nums">
                {{ group.rows.length }}개
              </span>
            </p>
          </CardContent>
        </Card>
      </CollapsibleContent>
    </Collapsible>

    <Button
      variant="outline"
      size="lg"
      class="mt-12 h-14 w-full rounded-2xl text-base"
      :disabled="busy"
      @click="emit('restart')"
    >
      처음으로
    </Button>
  </section>
</template>

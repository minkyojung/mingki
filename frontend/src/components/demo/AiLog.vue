<script setup lang="ts">
/** "AI가 n초 동안 확인한 내용": 접어 두고, 펼치면 AI가 한 일과 직접 고른 다른 규정 · 이유 */
import { CheckIcon, ChevronRightIcon } from '@lucide/vue'

import { Card, CardContent } from '@/components/ui/card'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import type { AiLog } from '@/lib/briefing'
import ExpandableList from './ExpandableList.vue'

defineProps<{ log: AiLog }>()
</script>

<template>
  <Collapsible>
    <CollapsibleTrigger
      class="group inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
    >
      AI가 {{ log.seconds === null ? '' : `${log.seconds}초 동안 ` }}확인한 내용
      <ChevronRightIcon class="size-4 transition-transform group-data-[state=open]:rotate-90" />
    </CollapsibleTrigger>
    <CollapsibleContent>
      <Card class="mt-3 rounded-2xl py-5 ring-foreground/5">
        <CardContent class="space-y-3 px-5">
          <p class="flex gap-2">
            <CheckIcon class="mt-0.5 size-4 shrink-0 text-brand" stroke-width="2.5" />바뀐 조항
            {{ log.requirementCount }}곳을 찾았어요
          </p>
          <p class="flex gap-2">
            <CheckIcon class="mt-0.5 size-4 shrink-0 text-brand" stroke-width="2.5" />상품
            {{ log.productCount }}개를 확인했어요
          </p>
          <div class="flex gap-2">
            <CheckIcon class="mt-0.5 size-4 shrink-0 text-brand" stroke-width="2.5" />
            <div class="flex-1">
              <template v-if="log.lookups.length">
                판단에 필요한 다른 규정 {{ log.lookups.length }}곳을 직접 골라 읽었어요
                <div class="mt-2 text-muted-foreground">
                  <ExpandableList :items="log.lookups" unit="곳">
                    <template #default="{ item }">
                      {{ item.label }}
                      <span class="text-muted-foreground/70">· {{ item.reason }}</span>
                    </template>
                  </ExpandableList>
                </div>
              </template>
              <template v-else>다른 규정은 더 읽지 않아도 됐어요</template>
            </div>
          </div>
          <p class="flex gap-2">
            <CheckIcon class="mt-0.5 size-4 shrink-0 text-brand" stroke-width="2.5" />
            {{
              log.allPassed
                ? '수정안을 검사했어요 · 모두 통과'
                : '수정안 검사에서 통과하지 못한 항목이 있어요'
            }}
          </p>
        </CardContent>
      </Card>
    </CollapsibleContent>
  </Collapsible>
</template>

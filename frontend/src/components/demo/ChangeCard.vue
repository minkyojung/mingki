<script setup lang="ts">
/**
 * 바뀐 조항 하나: 머리(무엇이 어떻게) + 바꿀 상품마다 체크.
 * 상품을 펼치면 바뀌는 값 · AI 제안 · 조치 · 이유와 [편집]. 고친 글은 초안으로 남고 확인 때 수정 요청으로 간다.
 * 다시 써 온 제안은 [확인하기] · [편집] 중 고른다.
 */
import { ArrowRightIcon, ChevronRightIcon, PencilIcon } from '@lucide/vue'
import { reactive, ref } from 'vue'

import { Button } from '@/components/ui/button'
import { Card, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import type { ChangeGroup, ChangeRow } from '@/lib/briefing'
import { actionsText, draftOf, OUTCOME, parseActions, sameDraft, type Draft } from '@/lib/revision'

const props = defineProps<{
  group: ChangeGroup
  excluded: Set<string>
  drafts: Record<string, Draft>
  /** 수정 요청을 AI가 받지 않은 이유 (proposalId별) */
  errors: Record<string, string>
  busy: boolean
}>()
const emit = defineEmits<{
  toggle: [keys: string[], on: boolean]
  save: [key: string, draft: Draft]
  discard: [key: string]
  confirm: [keys: string[]]
}>()

const TONE = {
  brand: 'bg-brand-soft text-accent-foreground',
  warn: 'bg-amber-50 text-amber-800',
  danger: 'bg-destructive/10 text-destructive',
}

const checked = (row: ChangeRow) => !props.excluded.has(row.key) && !row.failed
const outcome = (row: ChangeRow) =>
  row.revisionOutcome === 'none' ? null : OUTCOME[row.revisionOutcome]
function badge(row: ChangeRow) {
  if (row.failed) return { label: '검사 실패', tone: 'danger' as const }
  if (props.drafts[row.key]) return { label: '편집함', tone: 'brand' as const }
  return outcome(row)
}

// 편집은 한 번에 한 줄. 지금 보이는 글(초안이 있으면 초안)에서 시작한다
const editing = ref<string | null>(null)
const form = reactive({ proposed: '', actions: '' })

function startEdit(row: ChangeRow) {
  const from = props.drafts[row.key] ?? draftOf(row)
  form.proposed = from.proposed
  form.actions = actionsText(from.actions)
  editing.value = row.key
}

function save(row: ChangeRow) {
  const edited = { proposed: form.proposed, actions: parseActions(form.actions) }
  // AI가 쓴 글 그대로면 보낼 게 없다
  if (sameDraft(edited, draftOf(row))) emit('discard', row.key)
  else emit('save', row.key, { ...edited, baseVersion: row.version })
  editing.value = null
}

const fieldClass =
  'mt-1.5 w-full resize-y rounded-xl border border-input bg-card px-3 py-2 leading-relaxed outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50'
</script>

<template>
  <Card class="gap-0 rounded-2xl py-0 ring-foreground/5">
    <CardHeader class="gap-2 border-b py-5">
      <CardTitle class="text-sm font-medium text-muted-foreground">{{ group.title }}</CardTitle>
      <p class="flex flex-wrap items-center gap-x-2 text-xl font-semibold">
        <span
          v-if="group.added"
          class="rounded-md bg-brand-soft px-2 py-0.5 text-base text-accent-foreground"
        >
          새로 생겼어요
        </span>
        <template v-else-if="group.before && group.after">
          <span class="font-normal text-muted-foreground line-through">{{ group.before }}</span>
          <ArrowRightIcon class="size-4 text-muted-foreground" />
          <span class="text-brand">{{ group.after }}</span>
        </template>
      </p>
    </CardHeader>

    <div class="divide-y">
      <Collapsible
        v-for="row in group.rows"
        :key="row.key"
        :default-open="!!(drafts[row.key] || outcome(row) || errors[row.proposalId])"
      >
        <div class="flex items-center gap-3 px-6 py-4 transition-colors hover:bg-muted/50">
          <Checkbox
            class="size-5 rounded-md"
            :model-value="checked(row)"
            :disabled="row.failed || busy"
            :aria-label="`${row.productName} 바꾸기`"
            @update:model-value="(on) => emit('toggle', [row.key], on === true)"
          />
          <CollapsibleTrigger class="group flex min-w-0 flex-1 items-center gap-2 text-left">
            <span class="truncate" :class="{ 'text-muted-foreground': !checked(row) }">
              {{ row.productName }}
            </span>
            <span
              v-if="badge(row)"
              class="shrink-0 rounded-md px-1.5 py-0.5 text-xs font-medium"
              :class="TONE[badge(row)!.tone]"
            >
              {{ badge(row)!.label }}
            </span>
            <ChevronRightIcon
              class="ml-auto size-4 shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-90"
            />
          </CollapsibleTrigger>
        </div>

        <CollapsibleContent class="px-6 pb-5 pl-14 text-sm">
          <p class="font-medium">{{ row.change }}</p>

          <!-- 편집 -->
          <form v-if="editing === row.key" class="mt-3 space-y-3" @submit.prevent="save(row)">
            <label class="block">
              <span class="text-xs font-medium text-muted-foreground">제안</span>
              <textarea v-model="form.proposed" rows="4" :class="fieldClass" />
            </label>
            <label class="block">
              <span class="text-xs font-medium text-muted-foreground">조치 · 한 줄에 하나</span>
              <textarea v-model="form.actions" rows="4" :class="fieldClass" />
            </label>
            <p class="text-xs leading-relaxed text-muted-foreground">
              바뀌는 값 · 시행일 · 적용 범위는 그대로예요. 확인하기를 누르면 AI가 고친 글대로 다시
              만들어요.
            </p>
            <div class="flex justify-end gap-2">
              <Button type="button" variant="ghost" size="sm" @click="editing = null">취소</Button>
              <Button type="submit" size="sm">저장</Button>
            </div>
          </form>

          <template v-else>
            <!-- 고친 글 (확인 때 보낸다) -->
            <div v-if="drafts[row.key]" class="mt-3 rounded-xl bg-brand-soft/60 p-3.5">
              <p class="text-xs font-medium text-accent-foreground">
                고친 내용 · 확인하기를 누르면 AI가 이대로 다시 만들어요
              </p>
              <p class="mt-1.5 leading-relaxed">{{ drafts[row.key]!.proposed }}</p>
              <ul class="mt-2 list-disc space-y-1 pl-5 leading-relaxed">
                <li v-for="action in drafts[row.key]!.actions" :key="action">{{ action }}</li>
              </ul>
            </div>

            <template v-else>
              <p
                v-if="outcome(row)"
                class="mt-3 rounded-xl p-3.5 text-[13px] leading-relaxed"
                :class="TONE[outcome(row)!.tone]"
              >
                <span class="font-semibold">{{ outcome(row)!.label }}</span>
                <template v-if="row.revisionResponse"> · {{ row.revisionResponse }}</template>
              </p>
              <div v-if="row.proposed" class="mt-3">
                <p class="text-xs font-medium text-muted-foreground">제안</p>
                <p
                  class="mt-1 leading-relaxed"
                  :class="{
                    '-mx-2 rounded-lg bg-brand-soft/50 px-2 py-1':
                      row.changedFields.includes('recommendation'),
                  }"
                >
                  {{ row.proposed }}
                </p>
              </div>
              <div v-if="row.actions.length" class="mt-3">
                <p class="text-xs font-medium text-muted-foreground">조치</p>
                <ul
                  class="mt-1 list-disc space-y-1 pl-5 leading-relaxed"
                  :class="{
                    'rounded-lg bg-brand-soft/50 py-1 pr-2': row.changedFields.includes('actions'),
                  }"
                >
                  <li v-for="action in row.actions" :key="action">{{ action }}</li>
                </ul>
              </div>
            </template>

            <p class="mt-3 text-[13px] leading-relaxed text-muted-foreground">
              <span class="font-medium">판단 이유</span> · {{ row.reason }}
            </p>
            <p v-if="row.failed" class="mt-2 text-[13px] text-destructive">
              근거 검사를 통과하지 못해 반영할 수 없어요. 이 상품은 빼고 진행해요.
            </p>
            <p v-if="errors[row.proposalId]" role="alert" class="mt-2 text-[13px] text-destructive">
              {{ errors[row.proposalId] }}
            </p>

            <div v-if="!row.failed" class="mt-4 flex flex-wrap gap-2">
              <template v-if="drafts[row.key]">
                <Button variant="outline" size="sm" :disabled="busy" @click="startEdit(row)">
                  <PencilIcon />다시 편집
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  :disabled="busy"
                  @click="emit('discard', row.key)"
                >
                  편집 취소
                </Button>
              </template>
              <template v-else>
                <!-- 다시 써 온 제안은 여기서 바로 확정할 수 있다 -->
                <Button
                  v-if="outcome(row)"
                  size="sm"
                  :disabled="busy || !checked(row)"
                  @click="emit('confirm', [row.key])"
                >
                  확인하기
                </Button>
                <Button variant="outline" size="sm" :disabled="busy" @click="startEdit(row)">
                  <PencilIcon />편집
                </Button>
              </template>
            </div>
          </template>
        </CollapsibleContent>
      </Collapsible>
    </div>
  </Card>
</template>

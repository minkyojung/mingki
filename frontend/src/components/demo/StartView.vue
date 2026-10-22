<script setup lang="ts">
/** 시작: 무엇이 바뀌었는지 → 올릴 원문 두 개 → [영향받는 상품 찾기] → 이후 진행 순서 */
import {
  ArrowRightIcon,
  FileTextIcon,
  ListChecksIcon,
  PackageSearchIcon,
  ScanSearchIcon,
} from '@lucide/vue'
import oldRegulation from '@regulations/credit-loan-ops/v3.md?raw'
import newRegulation from '@regulations/credit-loan-ops/v4.md?raw'

import { demoRegulation } from '@/api/tasks'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { formatDate, withSubject } from '@/lib/briefing'

defineProps<{ busy: boolean }>()
const emit = defineEmits<{ start: [] }>()

const { title, oldVersion, newVersion, effectiveFrom } = demoRegulation

// 화면에 보여줄 숫자는 번들에 이미 들어 있는 원문에서 읽는다 (API 호출 없음)
const field = (text: string, key: string) =>
  text.match(new RegExp(`^${key}:\\s*(.+)$`, 'm'))?.[1]?.trim() ?? ''
const dotDate = (iso: string) => iso.replaceAll('-', '.')

const documents = [
  { version: oldVersion, label: '현행', date: dotDate(field(oldRegulation, 'effective_from')) },
  { version: newVersion, label: '개정', date: dotDate(effectiveFrom) },
]
const productCount = field(newRegulation, 'applies_to').split(',').filter(Boolean).length
const articleCount = newRegulation.match(/^### 제\d+조/gm)?.length ?? 0

const flow = [
  { icon: ScanSearchIcon, label: '바뀐 조항 찾기' },
  { icon: PackageSearchIcon, label: '상품마다 영향 판단' },
  { icon: ListChecksIcon, label: '확인 후 반영' },
]
</script>

<template>
  <section class="stage">
    <p
      class="inline-flex items-center self-start rounded-full bg-brand-soft px-3 py-1 text-sm font-medium text-accent-foreground"
    >
      {{ oldVersion }} → {{ newVersion }} · {{ formatDate(effectiveFrom) }} 시행
    </p>
    <h1 class="mt-4 text-[2rem] leading-tight font-bold">
      {{ withSubject(title) }}<br />바뀌었어요
    </h1>
    <p class="mt-3 leading-relaxed text-muted-foreground">
      상품 {{ productCount }}개에 적용되는 규정이에요. 바뀐 내용이 어느 상품에 영향을 주는지 AI가
      찾아 드려요.
    </p>

    <!-- 올릴 원문 -->
    <div class="mt-8 rounded-2xl bg-card p-2 shadow-xs ring-1 ring-foreground/5">
      <div class="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2">
        <div
          v-for="(doc, i) in documents"
          :key="doc.version"
          class="flex items-center gap-3 rounded-xl p-3"
          :class="i === 1 ? 'order-3 bg-brand-soft/60' : 'bg-muted/60'"
        >
          <span
            class="grid size-9 shrink-0 place-items-center rounded-lg"
            :class="i === 1 ? 'bg-brand text-white' : 'bg-card text-muted-foreground'"
          >
            <FileTextIcon class="size-4" />
          </span>
          <div class="min-w-0 text-sm">
            <p class="font-semibold">
              {{ doc.version }}
              <span class="font-normal text-muted-foreground">· {{ doc.label }}</span>
            </p>
            <p class="truncate text-xs text-muted-foreground">{{ doc.date }} 시행</p>
          </div>
        </div>
        <ArrowRightIcon class="order-2 size-4 text-muted-foreground" />
      </div>
      <p class="flex gap-4 px-3 pt-3 pb-1.5 text-xs text-muted-foreground">
        <span
          >적용 상품 <b class="font-semibold text-foreground">{{ productCount }}개</b></span
        >
        <span
          >조문 <b class="font-semibold text-foreground">{{ articleCount }}개</b></span
        >
      </p>
    </div>

    <Button
      size="lg"
      class="group mt-6 h-14 w-full rounded-2xl text-base shadow-lg shadow-brand/20"
      :disabled="busy"
      @click="emit('start')"
    >
      <Spinner v-if="busy" />
      영향받는 상품 찾기
      <ArrowRightIcon v-if="!busy" class="transition-transform group-hover:translate-x-0.5" />
    </Button>

    <!-- 이후 진행 순서 -->
    <ol class="mt-10 grid grid-cols-3 gap-2 text-center text-xs text-muted-foreground">
      <li v-for="(step, i) in flow" :key="step.label" class="flex flex-col items-center gap-2">
        <span
          class="relative grid size-10 place-items-center rounded-full bg-card ring-1 ring-foreground/5"
        >
          <component :is="step.icon" class="size-4 text-brand" />
          <span
            class="absolute -top-1 -right-1 grid size-4 place-items-center rounded-full bg-brand text-[10px] font-semibold text-white"
          >
            {{ i + 1 }}
          </span>
        </span>
        {{ step.label }}
      </li>
    </ol>
  </section>
</template>

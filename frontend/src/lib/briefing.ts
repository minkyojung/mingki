/** 백엔드 응답을 화면(시안 v2)에 보일 모양으로 바꾸는 순수 함수. 컴포넌트는 이 결과를 그리기만 한다. */

import type { Proposal, Requirement, RevisionOutcome, TaskEvent, TaskStatus } from '@/api/types'
import { proposalIdOf } from './revision'

const SUFFIX: Record<string, string> = {
  percent: '%',
  percent_point: '%p',
  months: '개월',
  days: '일',
  years: '년',
  count: '회',
  score: '점',
}

/** 규칙 값 표시: ratio 0.35 → 35%, age 60 → 만 60세, bool true → 켜짐 */
export function formatValue(value: string | null, unit: string | null) {
  if (value === null) return ''
  if (unit === 'bool') return value === 'true' ? '켜짐' : '꺼짐'
  const n = Number(value)
  if (!Number.isFinite(n)) return value
  if (unit === 'ratio') return `${round(n * 100)}%`
  if (unit === 'age') return `만 ${n}세`
  if (unit === 'krw') return `${n.toLocaleString('ko-KR')}원`
  return `${round(n)}${SUFFIX[unit ?? ''] ?? ''}`
}

const round = (n: number) => Math.round(n * 100) / 100

/** '2026-12-01' → '12월 1일' */
export function formatDate(iso: string | null) {
  const [, month, day] = iso?.split('-') ?? []
  return month && day ? `${Number(month)}월 ${Number(day)}일` : ''
}

/** 받침에 맞는 주격 조사: '운영기준' → '운영기준이', '기준표' → '기준표가' */
export function withSubject(word: string) {
  const code = word.charCodeAt(word.length - 1) - 0xac00
  return word + (code >= 0 && code < 11172 && code % 28 === 0 ? '가' : '이')
}

/** 조항 ID → 이름: '15-2' → '제15조의2', 'supp-1' → '부칙 제1조' */
export function articleLabel(id: string) {
  if (id.startsWith('supp-')) return `부칙 제${id.slice(5)}조`
  const [main, sub] = id.split('-')
  return sub ? `제${main}조의${sub}` : `제${main}조`
}

export interface ChangeRow {
  key: string
  productId: string
  productName: string
  /** '40% → 35%' 또는 '새 규칙 추가' */
  change: string
  reason: string
  /** 수정 요청 단위 `{reqKey}:{productId}` · 버전 */
  proposalId: string
  version: number
  /** AI가 쓴 제안 문장 · 조치 (수정 요청으로 다시 쓰는 글) */
  proposed: string
  actions: string[]
  revisionOutcome: RevisionOutcome
  revisionResponse: string | null
  changedFields: string[]
  /** 근거 검사 실패: 반영할 수 없고 제외만 된다 */
  failed: boolean
}

export interface ChangeGroup {
  reqKey: string
  title: string
  /** 상품마다 같은 값으로 바뀌면 그 값. 조항이 새로 생겼거나 상품마다 다르면 null */
  before: string | null
  after: string | null
  added: boolean
  rows: ChangeRow[]
}

export interface Note {
  key: string
  productName: string
  topic: string
  reason: string
}

export interface Briefing {
  /** 바꿀 상품 수 (상품 하나가 여러 조항으로 바뀌어도 하나) */
  productCount: number
  groups: ChangeGroup[]
  needsCheck: Note[]
  keep: Note[]
  changeKeys: string[]
  effectiveDate: string | null
}

/** 확인 화면: 최신 제안을 바뀐 조항별로 묶는다. 완료 화면은 반영된 제안만 넘겨 같은 모양을 쓴다 */
export function buildBriefing(requirements: Requirement[], proposals: Proposal[]): Briefing {
  const latest = proposals.filter((p) => p.latest)
  const changes = latest.filter((p) => p.impact === 'change_required')
  const requirement = new Map(requirements.map((r) => [r.reqKey, r]))
  const reqKeys = [
    ...new Set([...requirements.map((r) => r.reqKey), ...latest.map((p) => p.reqKey)]),
  ]
  const topic = (reqKey: string) => {
    const r = requirement.get(reqKey)
    return r ? (r.detail.title ?? r.detail.article) : reqKey
  }
  const notes = (impact: Proposal['impact']) =>
    reqKeys.flatMap((reqKey) =>
      latest
        .filter((p) => p.reqKey === reqKey && p.impact === impact)
        .map((p) => ({
          key: p.proposalKey,
          productName: p.productName,
          topic: topic(reqKey),
          reason: p.reason,
        })),
    )
  const groups = reqKeys.flatMap((reqKey): ChangeGroup[] => {
    const rows = changes.filter((p) => p.reqKey === reqKey)
    if (!rows.length) return []
    const r = requirement.get(reqKey)
    const added = r?.detail.change === 'added'
    // 머리 값: 상품마다 같은 값으로 바뀌면 그 값(코드가 계산한 값), 아니면 요건의 전후 값
    const values = new Map(rows.map((p) => [valueChange(p), p]))
    const only = values.size === 1 ? [...values.values()][0] : undefined
    return [
      {
        reqKey,
        title: topic(reqKey),
        before: added
          ? null
          : only
            ? formatValue(only.currentValue, only.unit)
            : (r?.detail.old_value ?? null),
        after: added
          ? null
          : only
            ? formatValue(only.proposedValue, only.unit)
            : (r?.detail.new_value ?? null),
        added,
        rows: rows.map((p) => ({
          key: p.proposalKey,
          productId: p.productId,
          productName: p.productName,
          change: added ? '새 규칙 추가' : valueChange(p),
          reason: p.reason,
          proposalId: proposalIdOf(p),
          version: p.version ?? 1,
          proposed: p.proposed ?? '',
          actions: p.followUp ?? [],
          revisionOutcome: p.revisionOutcome ?? 'none',
          revisionResponse: p.revisionResponse ?? null,
          changedFields: p.changedFields ?? [],
          failed: p.verifyStatus === 'FAILED',
        })),
      },
    ]
  })
  return {
    productCount: new Set(changes.map((p) => p.productId)).size,
    groups,
    needsCheck: notes('needs_check'),
    keep: notes('keep'),
    changeKeys: changes.map((p) => p.proposalKey),
    effectiveDate: changes[0]?.effectiveDate ?? requirements[0]?.effectiveDate ?? null,
  }
}

function valueChange(p: Proposal) {
  return `${formatValue(p.currentValue, p.unit)} → ${formatValue(p.proposedValue, p.unit)}`
}

/** 완료 화면: 실제로 반영된 제안 */
export function appliedProposals(proposals: Proposal[]) {
  return proposals.filter((p) => p.latest && p.decision === 'APPLY' && p.apply?.status === 'DONE')
}

export interface LookupLine {
  key: string
  label: string
  reason: string
}

/** AI가 고른 조회 (TOOL_PLAN). 여러 요건에서 같은 조항을 고르면 한 번만 */
export function lookupLines(events: TaskEvent[]): LookupLine[] {
  const lines = new Map<string, LookupLine>()
  for (const event of events) {
    if (event.type !== 'TOOL_PLAN') continue
    for (const x of event.payload.lookups ?? []) {
      const key =
        x.tool === 'get_article' ? `${x.regulationId}/${x.articleId}` : `search:${x.query}`
      if (lines.has(key)) continue
      const label =
        x.tool === 'get_article' && x.articleId
          ? `${x.regulationTitle ?? x.regulationId} ${articleLabel(x.articleId)}` +
            (x.articleTitle ? `(${x.articleTitle})` : '')
          : `'${x.query}' 검색`
      lines.set(key, { key, label, reason: x.reason })
    }
  }
  return [...lines.values()]
}

export interface AiLog {
  /** 시작부터 확인 대기까지 걸린 시간 */
  seconds: number | null
  requirementCount: number
  productCount: number
  lookups: LookupLine[]
  allPassed: boolean
}

export function buildAiLog(
  events: TaskEvent[],
  requirements: Requirement[],
  proposals: Proposal[],
): AiLog {
  const start = events[0]?.createdAt
  const ready = events.find((e) => e.type === 'APPROVAL_REQUIRED')?.createdAt
  const latest = proposals.filter((p) => p.latest)
  return {
    seconds: start && ready ? Math.round((Date.parse(ready) - Date.parse(start)) / 1000) : null,
    requirementCount: requirements.length,
    productCount: new Set(latest.map((p) => p.productId)).size,
    lookups: lookupLines(events),
    allPassed: latest
      .filter((p) => p.impact === 'change_required')
      .every((p) => p.verifyStatus === 'PASSED'),
  }
}

export type StepState = 'done' | 'active' | 'todo'

export interface Step {
  label: string
  detail?: string
  state: StepState
}

const STAGE: Partial<Record<TaskStatus, number>> = {
  EXTRACTING: 0,
  LOCATING: 1,
  DESIGNING: 2,
  VERIFYING: 2,
}

/** 작업 중 화면의 단계. 상태로 큰 단계를, 이벤트로 그 안의 진행(상품 불러옴, 다른 규정 읽음)을 본다 */
export function progressSteps(
  status: TaskStatus,
  verifyRound: number,
  requirementCount: number,
  events: TaskEvent[],
): Step[] {
  const stage = STAGE[status] ?? 3
  const products = events.find(
    (e) => e.type === 'TOOL_END' && e.payload.tool === 'get_products' && e.payload.ok,
  )
  const productCount = products?.payload.summary?.match(/^(\d+)건/)?.[1]
  const loaded = stage > 1 || (stage === 1 && !!products)
  const lookups = lookupLines(events).length
  return [
    stage > 0
      ? { label: `바뀐 조항 ${requirementCount}곳을 찾았어요`, state: 'done' }
      : { label: '바뀐 조항을 찾고 있어요', state: 'active' },
    loaded
      ? {
          label: productCount ? `상품 ${productCount}개를 불러왔어요` : '상품을 불러왔어요',
          state: 'done',
        }
      : stage === 1
        ? { label: '상품을 불러오고 있어요', state: 'active' }
        : { label: '상품 불러오기', state: 'todo' },
    {
      label:
        stage > 1
          ? '상품마다 영향을 판단했어요'
          : loaded
            ? '상품마다 영향을 판단하고 있어요'
            : '상품마다 영향 판단하기',
      detail: lookups ? `다른 규정 ${lookups}곳을 더 읽었어요` : undefined,
      state: stage > 1 ? 'done' : loaded ? 'active' : 'todo',
    },
    stage > 2
      ? { label: '수정안을 만들고 검사했어요', state: 'done' }
      : stage === 2
        ? {
            label:
              verifyRound > 1
                ? `수정안을 다시 만들고 있어요 · ${verifyRound}회차`
                : '수정안을 만들고 검사하고 있어요',
            state: 'active',
          }
        : { label: '수정안 만들고 검사하기', state: 'todo' },
  ]
}

/** 반영 중: 큐 작업이 끝난 제안 수 (Agent가 아닌 큐의 TOOL_END는 node가 없다) */
export function appliedCount(events: TaskEvent[]) {
  return events.filter((e) => e.type === 'TOOL_END' && !e.payload.node && e.payload.ok).length
}

/** 첫 이벤트(since를 주면 그 type의 마지막 이벤트, 예: 확인 후 RESUMED)부터 지금까지 */
export function elapsedSeconds(events: TaskEvent[], now: number, since?: string) {
  const start = since
    ? [...events].reverse().find((e) => e.type === since)?.createdAt
    : events[0]?.createdAt
  return start ? Math.max(0, Math.round((now - Date.parse(start)) / 1000)) : 0
}

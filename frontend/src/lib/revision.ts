/** 제안 편집 · 수정 요청(docs/BACKEND_API.md 10.3)에 쓰는 순수 함수 */
import type { Proposal, RevisionOutcome } from '@/api/types'

/** 수정 요청 단위: Agent 제안 ID `{reqKey}:{productId}` */
export const proposalIdOf = (p: Pick<Proposal, 'reqKey' | 'productId'>) =>
  `${p.reqKey}:${p.productId}`

/** 사용자가 고친 글. 어느 버전을 보고 고쳤는지 함께 둔다 */
export interface Draft {
  proposed: string
  actions: string[]
  baseVersion: number
}

/** 화면 행(lib/briefing ChangeRow)의 지금 글 → 편집 시작점 */
export function draftOf(row: { proposed: string; actions: string[]; version: number }): Draft {
  return { proposed: row.proposed, actions: row.actions, baseVersion: row.version }
}

/** 조치 칸(한 줄에 하나) ↔ 목록 */
export const actionsText = (actions: string[]) => actions.join('\n')
export const parseActions = (text: string) =>
  text
    .split('\n')
    .map((line) => line.replace(/^\s*[-•·]\s*/, '').trim())
    .filter(Boolean)

export function sameDraft(
  a: Pick<Draft, 'proposed' | 'actions'>,
  b: Pick<Draft, 'proposed' | 'actions'>,
) {
  return (
    a.proposed.trim() === b.proposed.trim() && actionsText(a.actions) === actionsText(b.actions)
  )
}

/**
 * 고친 글 → 수정 요청 comment. 바뀐 칸만 그대로 적어 보낸다.
 * Agent는 "다시 써줘" 같은 모호한 요청을 받지 않으므로 무엇을 어떻게 바꿨는지 구체적으로 쓴다.
 */
export function revisionComment(before: Draft, after: Draft) {
  const parts = ['사용자가 제안을 직접 고쳤습니다. 아래 고친 내용을 그대로 반영해 주세요.']
  if (before.proposed.trim() !== after.proposed.trim())
    parts.push(`[제안 문장]\n${after.proposed.trim()}`)
  if (actionsText(before.actions) !== actionsText(after.actions))
    parts.push(`[조치]\n${after.actions.map((a) => `- ${a}`).join('\n')}`)
  parts.push('필수 값 · 시행일 · 적용 범위는 그대로 둡니다.')
  return parts.join('\n\n')
}

/** 다시 쓴 결과 표시 (none은 표시 없음) */
export const OUTCOME: Record<
  Exclude<RevisionOutcome, 'none'>,
  { label: string; tone: 'brand' | 'warn' | 'danger' }
> = {
  revised: { label: 'AI가 다시 썼어요', tone: 'brand' },
  cannot_apply: { label: '반영하지 못했어요', tone: 'warn' },
  needs_clarification: { label: '확인이 필요해요', tone: 'warn' },
  failed: { label: '검사를 통과하지 못했어요', tone: 'danger' },
}

/** 시연 화면이 쓰는 백엔드 API. 경로 · 본문은 docs/BACKEND_API.md, 흐름은 scripts/smoke.sh와 같다. */

import oldRegulation from '@regulations/credit-loan-ops/v3.md?raw'
import newRegulation from '@regulations/credit-loan-ops/v4.md?raw'
import { request, setToken } from './client'
import type {
  ProposalsResponse,
  Requirement,
  TaskDetail,
  TaskEvent,
  TaskStatus,
  TaskSummary,
} from './types'

/** 시연 계정 (backend V2__seed.sql). 화면은 로그인 화면 없이 이 계정으로 동작한다 */
const DEMO_ACCOUNT = { username: 'officer1', password: 'password' }

/** 시연에서 올리는 규정 (저장소 원문 그대로). 시작 화면 문구는 개정본 머리말에서 읽는다 */
export const demoRegulation = {
  title: frontmatter(newRegulation, 'title'),
  oldVersion: frontmatter(oldRegulation, 'version'),
  newVersion: frontmatter(newRegulation, 'version'),
  effectiveFrom: frontmatter(newRegulation, 'effective_from'),
}

function frontmatter(text: string, key: string) {
  return text.match(new RegExp(`^${key}:\\s*(.+)$`, 'm'))?.[1]?.trim() ?? ''
}

export async function login() {
  const { token } = await request<{ token: string }>('/auth/login', { json: DEMO_ACCOUNT })
  setToken(token)
}

export function resetDemo() {
  return request<unknown>('/demo/reset', { method: 'POST' })
}

export async function latestTask() {
  const tasks = await request<TaskSummary[]>('/ai/tasks?mine=false')
  return tasks.reduce<TaskSummary | null>(
    (latest, t) => (!latest || t.createdAt > latest.createdAt ? t : latest),
    null,
  )
}

export function startTask() {
  const form = new FormData()
  form.append('oldVersion', demoRegulation.oldVersion)
  form.append('newVersion', demoRegulation.newVersion)
  form.append(
    'oldFile',
    new Blob([oldRegulation], { type: 'text/markdown' }),
    `${demoRegulation.oldVersion}.md`,
  )
  form.append(
    'newFile',
    new Blob([newRegulation], { type: 'text/markdown' }),
    `${demoRegulation.newVersion}.md`,
  )
  return request<{ taskId: string; status: TaskStatus }>('/ai/tasks', { form })
}

export function getTask(id: string) {
  return request<TaskDetail>(`/ai/tasks/${id}`)
}

export function getEvents(id: string, after: number) {
  return request<TaskEvent[]>(`/ai/tasks/${id}/events?after=${after}`)
}

export async function getRequirements(id: string) {
  return (await request<{ requirements: Requirement[] }>(`/ai/tasks/${id}/requirements`))
    .requirements
}

export function getProposals(id: string) {
  return request<ProposalsResponse>(`/ai/tasks/${id}/proposals`)
}

/** 수정 요청: 제안 하나(요건 × 상품)의 글을 AI가 다시 쓴다. 접수(202)이지 완료가 아니다 */
export function requestRevision(
  id: string,
  body: { proposalId: string; baseVersion: number; comment: string },
) {
  return request<{ revisionId: number; proposalId: string; baseVersion: number; status: string }>(
    `/ai/tasks/${id}/revisions`,
    { json: body },
  )
}

/** 확인: 체크를 푼 제안만 제외하고 나머지를 반영한다 */
export function approve(id: string, excludedProposalKeys: string[]) {
  return request<unknown>(`/ai/tasks/${id}/review`, {
    json: { decision: 'APPROVE', excludedProposalKeys, comment: '시연 화면에서 확인' },
  })
}

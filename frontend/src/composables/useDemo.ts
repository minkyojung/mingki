/**
 * 시연 흐름 하나: 자동 로그인 → (최근 작업 이어보기) → 시작 → 1초마다 진행 확인 → 확인 (⇄ 수정 요청) → 반영 → 완료. 초기화.
 * 서버 호출은 api/tasks.ts, 화면 계산은 lib/briefing.ts. 여기서는 단계와 폴링만 다룬다.
 */

import { onScopeDispose, ref, shallowRef } from 'vue'

import { ApiError } from '@/api/client'
import * as api from '@/api/tasks'
import type { Proposal, Requirement, TaskDetail, TaskEvent, TaskStatus } from '@/api/types'
import { proposalIdOf } from '@/lib/revision'

export type Phase =
  'loading' | 'start' | 'running' | 'revising' | 'review' | 'publishing' | 'done' | 'failed'

/** 편집한 제안 하나를 다시 쓰게 하는 요청 */
export interface RevisionItem {
  proposalId: string
  comment: string
}

const POLL_MS = 1000

const PHASE: Record<TaskStatus, Phase> = {
  EXTRACTING: 'running',
  LOCATING: 'running',
  DESIGNING: 'running',
  VERIFYING: 'running',
  WAITING_REVIEW: 'review',
  PUBLISHING: 'publishing',
  DONE: 'done',
  NO_CHANGE: 'done',
  REJECTED: 'done',
  FAILED: 'failed',
}

/** 작업 중 상태라도 마지막 재개가 수정 요청이면 '다시 쓰는 중'이다 (새로고침해도 이어 보인다) */
export function phaseOf(status: TaskStatus, events: TaskEvent[]): Phase {
  const phase = PHASE[status]
  if (phase !== 'running') return phase
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i]!.type === 'RESUMED')
      return events[i]!.payload.decision === 'REQUEST_CHANGES' ? 'revising' : phase
  }
  return phase
}

export function useDemo() {
  const phase = ref<Phase>('loading')
  const task = shallowRef<TaskDetail | null>(null)
  const events = shallowRef<TaskEvent[]>([])
  const requirements = shallowRef<Requirement[]>([])
  const proposals = shallowRef<Proposal[]>([])
  const canApprove = ref(false)
  const error = ref<string | null>(null)
  const busy = ref(false)
  const now = ref(Date.now())
  /** 편집한 제안을 차례로 다시 쓰는 중: 몇 개 중 몇 개, 언제 시작 */
  const revising = ref<{ total: number; done: number; startedAt: number } | null>(null)
  /** 수정 요청을 Agent가 받지 않은 이유 (proposalId별, 예: 무엇을 바꿀지 모호함) */
  const revisionErrors = ref<Record<string, string>>({})

  // 시작 · 초기화마다 늘린다. 이전 작업을 보던 폴링의 늦은 응답은 버린다
  let run = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  // 지금 폴링이 멈추면(확인 대기 · 완료 · 실패 · 중단) 풀린다. 수정 요청을 하나씩 기다릴 때 쓴다
  let settle: (() => void) | undefined
  let settled: Promise<void> = Promise.resolve()

  function stop() {
    run++
    clearTimeout(timer)
    settle?.()
  }

  function clear() {
    task.value = null
    events.value = []
    requirements.value = []
    proposals.value = []
    canApprove.value = false
    revisionErrors.value = {}
  }

  function message(e: unknown) {
    return e instanceof Error ? e.message : String(e)
  }

  /** 토큰이 만료되면(8시간) 한 번 다시 로그인한다 */
  async function call<T>(fn: () => Promise<T>): Promise<T> {
    try {
      return await fn()
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) throw e
      await api.login()
      return fn()
    }
  }

  async function follow(id: string) {
    stop()
    if (task.value?.task.id !== id) clear() // 같은 작업을 이어 보면 받은 이벤트 다음부터
    const current = run
    let loadedFor: TaskStatus | null = null
    settled = new Promise((resolve) => (settle = resolve))
    const finish = settle
    const tick = async () => {
      try {
        const after = events.value.at(-1)?.seq ?? 0
        const [detail, fresh] = await Promise.all([
          call(() => api.getTask(id)),
          call(() => api.getEvents(id, after)),
        ])
        if (current !== run) return finish?.()
        const status = detail.task.status
        // 확인 · 반영 · 완료 화면은 요건과 제안이 필요하다. 상태가 바뀔 때 한 번 불러온다
        if (PHASE[status] !== 'running' && PHASE[status] !== 'failed' && loadedFor !== status) {
          const [reqs, res] = await Promise.all([
            call(() => api.getRequirements(id)),
            call(() => api.getProposals(id)),
          ])
          if (current !== run) return finish?.()
          requirements.value = reqs
          proposals.value = res.proposals
          canApprove.value = res.canApprove
          loadedFor = status
        }
        task.value = detail
        if (fresh.length) events.value = [...events.value, ...fresh]
        now.value = Date.now()
        phase.value = phaseOf(status, events.value)
        if (status === 'FAILED') error.value = detail.errorMessage ?? '작업이 실패했어요'
        if (['running', 'revising', 'publishing'].includes(phase.value))
          timer = setTimeout(tick, POLL_MS)
        else finish?.()
      } catch (e) {
        if (current !== run) return finish?.()
        error.value = message(e)
        phase.value = 'failed'
        finish?.()
      }
    }
    await tick()
  }

  async function init() {
    stop()
    phase.value = 'loading'
    error.value = null
    try {
      await api.login()
      const latest = await api.latestTask()
      if (latest) await follow(latest.id)
      else phase.value = 'start'
    } catch (e) {
      error.value = message(e)
      phase.value = 'failed'
    }
  }

  /** 화면 동작(시작 · 바꾸기 · 초기화) 실패는 지금 화면을 두고 오류만 보여 준다 */
  async function act(fn: () => Promise<void>) {
    busy.value = true
    error.value = null
    try {
      await fn()
    } catch (e) {
      error.value = message(e)
    } finally {
      busy.value = false
    }
  }

  const start = () =>
    act(async () => {
      const { taskId } = await call(() => api.startTask())
      await follow(taskId)
    })

  /** 체크를 푼 제안만 빼고 반영한다 */
  const approve = (excludedKeys: string[]) =>
    act(async () => {
      const id = task.value?.task.id
      if (!id) return
      await call(() => api.approve(id, excludedKeys))
      await follow(id)
    })

  /**
   * 편집한 제안을 하나씩 다시 쓰게 한다. 처리 중에는 다음 요청을 받지 않으므로(409)
   * 하나 보내고 확인 대기로 돌아올 때까지 기다린 뒤 다음을 보낸다. 버전은 보내는 순간의 현재 버전.
   */
  const revise = (items: RevisionItem[]) =>
    act(async () => {
      const id = task.value?.task.id
      if (!id || !items.length) return
      revisionErrors.value = {}
      revising.value = { total: items.length, done: 0, startedAt: Date.now() }
      try {
        for (const item of items) {
          const current = proposals.value.find(
            (p) => p.latest && p.impact !== 'keep' && proposalIdOf(p) === item.proposalId,
          )
          if (!current) continue
          const baseVersion = current.version ?? 1
          await call(() =>
            api.requestRevision(id, {
              proposalId: item.proposalId,
              baseVersion,
              comment: item.comment,
            }),
          )
          await follow(id)
          await settled
          if (phase.value === 'failed') return
          // 버전이 그대로면 Agent가 받지 않은 것 (확인 대기로 돌아오며 이유를 남긴다)
          const after = proposals.value.find((p) => p.latest && proposalIdOf(p) === item.proposalId)
          if ((after?.version ?? 1) <= baseVersion)
            revisionErrors.value = {
              ...revisionErrors.value,
              [item.proposalId]: task.value?.errorMessage ?? 'AI가 수정 요청을 받지 않았어요',
            }
          revising.value = { ...revising.value!, done: revising.value!.done + 1 }
        }
      } finally {
        revising.value = null
      }
    })

  const reset = () =>
    act(async () => {
      stop()
      await call(() => api.resetDemo())
      clear()
      phase.value = 'start'
    })

  /** 실패 화면의 다시 시도: 보던 작업이 있으면 이어서, 없으면 처음부터 */
  function retry() {
    error.value = null
    const id = task.value?.task.id
    return id ? follow(id) : init()
  }

  onScopeDispose(stop)

  return {
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
  }
}

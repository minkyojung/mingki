import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'

import { ApiError } from '@/api/client'
import * as api from '@/api/tasks'
import type { Proposal, TaskDetail, TaskEvent, TaskStatus } from '@/api/types'
import { phaseOf, useDemo } from './useDemo'

vi.mock('@/api/tasks', () => ({
  login: vi.fn(),
  latestTask: vi.fn(),
  startTask: vi.fn(),
  getTask: vi.fn(),
  getEvents: vi.fn(),
  getRequirements: vi.fn(),
  getProposals: vi.fn(),
  approve: vi.fn(),
  requestRevision: vi.fn(),
  resetDemo: vi.fn(),
}))

const mocked = vi.mocked(api)

const detail = (status: TaskStatus): TaskDetail => ({
  task: {
    id: 'T1',
    title: '',
    status,
    currentNode: null,
    verifyRound: 1,
    createdAt: '',
    updatedAt: '',
  },
  errorMessage: status === 'FAILED' ? 'MCP 응답 없음' : null,
  counts: { requirements: 3, proposals: 0 },
})

/** 작업 상태를 차례로 돌려준다. 마지막 상태는 계속 */
function statuses(...list: TaskStatus[]) {
  mocked.getTask.mockImplementation(async () => detail(list.length > 1 ? list.shift()! : list[0]!))
}

function setup() {
  const scope = effectScope()
  const demo = scope.run(() => useDemo())!
  return { demo, scope }
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.resetAllMocks()
  mocked.latestTask.mockResolvedValue(null)
  mocked.startTask.mockResolvedValue({ taskId: 'T1', status: 'EXTRACTING' })
  mocked.getEvents.mockResolvedValue([])
  mocked.getRequirements.mockResolvedValue([])
  mocked.getProposals.mockResolvedValue({ canApprove: true, proposals: [] })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('시연 흐름', () => {
  it('시작하면 확인 대기까지 1초마다 확인하고, 확인 화면에서 멈춘다', async () => {
    const { demo } = setup()
    await demo.init()
    expect(demo.phase.value).toBe('start')

    statuses('EXTRACTING', 'LOCATING', 'WAITING_REVIEW')
    await demo.start()
    expect(demo.phase.value).toBe('running')
    await vi.advanceTimersByTimeAsync(2000)
    expect(demo.phase.value).toBe('review')
    expect(mocked.getProposals).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(5000)
    expect(mocked.getTask).toHaveBeenCalledTimes(3) // 확인 화면에서는 더 묻지 않는다
  })

  it('바꾸기는 체크를 푼 제안만 빼고 보내고, 반영이 끝날 때까지 본다', async () => {
    const { demo } = setup()
    statuses('WAITING_REVIEW')
    mocked.latestTask.mockResolvedValue(detail('WAITING_REVIEW').task)
    await demo.init()
    expect(demo.phase.value).toBe('review')

    statuses('PUBLISHING', 'DONE')
    await demo.approve(['P005:DSR_LIMIT'])
    expect(mocked.approve).toHaveBeenCalledWith('T1', ['P005:DSR_LIMIT'])
    expect(demo.phase.value).toBe('publishing')
    await vi.advanceTimersByTimeAsync(1000)
    expect(demo.phase.value).toBe('done')
  })

  it('받은 이벤트 다음부터만 다시 받는다', async () => {
    const { demo } = setup()
    statuses('LOCATING')
    mocked.getEvents.mockResolvedValueOnce([{ seq: 7, type: 'STEP', payload: {}, createdAt: '' }])
    await demo.start()
    await vi.advanceTimersByTimeAsync(1000)
    expect(mocked.getEvents).toHaveBeenLastCalledWith('T1', 7)
    expect(demo.events.value).toHaveLength(1)
  })

  it('토큰이 만료되면 다시 로그인하고 이어 간다', async () => {
    const { demo } = setup()
    statuses('LOCATING')
    mocked.getEvents.mockRejectedValueOnce(new ApiError(401, 'UNAUTHORIZED', '인증이 필요합니다'))
    await demo.start()
    expect(mocked.login).toHaveBeenCalledTimes(1)
    expect(demo.phase.value).toBe('running')
  })

  it('작업 실패는 서버가 남긴 이유와 함께 실패 화면으로', async () => {
    const { demo } = setup()
    statuses('FAILED')
    await demo.start()
    expect(demo.phase.value).toBe('failed')
    expect(demo.error.value).toBe('MCP 응답 없음')
  })

  it('바꾸기가 거절되면 확인 화면에 남아 이유를 보여 준다', async () => {
    const { demo } = setup()
    statuses('WAITING_REVIEW')
    mocked.latestTask.mockResolvedValue(detail('WAITING_REVIEW').task)
    await demo.init()
    mocked.approve.mockRejectedValueOnce(
      new ApiError(409, 'CONFLICT', '검증을 통과하지 못한 제안이 있습니다'),
    )
    await demo.approve([])
    expect(demo.phase.value).toBe('review')
    expect(demo.error.value).toBe('검증을 통과하지 못한 제안이 있습니다')
  })

  it('초기화하면 진행 중이던 확인을 멈추고 시작 화면으로', async () => {
    const { demo } = setup()
    statuses('LOCATING')
    await demo.start()
    await demo.reset()
    expect(demo.phase.value).toBe('start')
    const calls = mocked.getTask.mock.calls.length
    await vi.advanceTimersByTimeAsync(5000)
    expect(mocked.getTask.mock.calls.length).toBe(calls)
  })

  it('화면을 떠나면 확인을 멈춘다', async () => {
    const { demo, scope } = setup()
    statuses('LOCATING')
    await demo.start()
    scope.stop()
    const calls = mocked.getTask.mock.calls.length
    await vi.advanceTimersByTimeAsync(5000)
    expect(mocked.getTask.mock.calls.length).toBe(calls)
  })
})

/** 수정 요청 대상 제안 (요건 × 상품 하나) */
const proposalV = (productId: string, version: number): Proposal => ({
  proposalKey: `${productId}:DSR_LIMIT`,
  reqKey: 'REQ-8',
  productId,
  productName: productId,
  ruleId: 'R001',
  impact: 'change_required',
  unit: 'ratio',
  currentValue: '0.40',
  proposedValue: '0.35',
  effectiveDate: '2026-12-01',
  reason: '',
  version,
  verifyStatus: 'PASSED',
  latest: true,
  decision: null,
  apply: null,
})

const resumed = (seq: number, decision: string): TaskEvent => ({
  seq,
  type: 'RESUMED',
  payload: { decision },
  createdAt: '',
})

describe('수정 요청', () => {
  it('마지막 재개가 수정 요청이면 작업 중 상태를 다시 쓰는 중으로 본다', () => {
    expect(phaseOf('DESIGNING', [resumed(1, 'REQUEST_CHANGES')])).toBe('revising')
    expect(phaseOf('DESIGNING', [])).toBe('running')
    expect(phaseOf('PUBLISHING', [resumed(1, 'APPROVE')])).toBe('publishing')
    expect(phaseOf('WAITING_REVIEW', [resumed(1, 'REQUEST_CHANGES')])).toBe('review')
  })

  it('편집한 제안을 하나씩 보내고, 확인 대기로 돌아오면 다음을 보낸다', async () => {
    const { demo } = setup()
    statuses('WAITING_REVIEW')
    mocked.latestTask.mockResolvedValue(detail('WAITING_REVIEW').task)
    mocked.getProposals.mockResolvedValue({
      canApprove: true,
      proposals: [proposalV('P001', 1), proposalV('P002', 1)],
    })
    await demo.init()

    statuses('DESIGNING', 'WAITING_REVIEW', 'DESIGNING', 'WAITING_REVIEW')
    mocked.getEvents.mockResolvedValue([resumed(1, 'REQUEST_CHANGES')])
    mocked.getProposals
      .mockResolvedValueOnce({
        canApprove: true,
        proposals: [proposalV('P001', 2), proposalV('P002', 1)],
      })
      .mockResolvedValueOnce({
        canApprove: true,
        proposals: [proposalV('P001', 2), proposalV('P002', 2)],
      })

    const done = demo.revise([
      { proposalId: 'REQ-8:P001', comment: '조치에 안내 문구 추가' },
      { proposalId: 'REQ-8:P002', comment: '제안 문장 짧게' },
    ])
    await vi.advanceTimersByTimeAsync(0)
    expect(demo.phase.value).toBe('revising')
    expect(mocked.requestRevision).toHaveBeenCalledTimes(1) // 처리 중에는 다음을 보내지 않는다
    await vi.advanceTimersByTimeAsync(3000)
    await done

    expect(mocked.requestRevision.mock.calls).toEqual([
      ['T1', { proposalId: 'REQ-8:P001', baseVersion: 1, comment: '조치에 안내 문구 추가' }],
      ['T1', { proposalId: 'REQ-8:P002', baseVersion: 1, comment: '제안 문장 짧게' }],
    ])
    expect(demo.phase.value).toBe('review')
    expect(demo.revising.value).toBeNull()
    expect(demo.revisionErrors.value).toEqual({})
  })

  it('AI가 받지 않으면(버전 그대로) 그 제안에 이유를 남긴다', async () => {
    const { demo } = setup()
    mocked.latestTask.mockResolvedValue(detail('WAITING_REVIEW').task)
    mocked.getProposals.mockResolvedValue({ canApprove: true, proposals: [proposalV('P001', 1)] })
    mocked.getTask.mockResolvedValue({
      ...detail('WAITING_REVIEW'),
      errorMessage: '무엇을 바꿀지 구체적으로 적어주세요',
    })
    await demo.init()

    await demo.revise([{ proposalId: 'REQ-8:P001', comment: '다시 써줘' }])
    expect(demo.revisionErrors.value).toEqual({
      'REQ-8:P001': '무엇을 바꿀지 구체적으로 적어주세요',
    })
    expect(demo.phase.value).toBe('review')
  })
})

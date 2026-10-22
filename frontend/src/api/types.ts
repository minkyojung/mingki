/** 백엔드 응답 모양 (docs/BACKEND_API.md). 필드 이름은 백엔드 그대로, 화면에서 쓰는 것만 적는다. */

export type TaskStatus =
  | 'EXTRACTING'
  | 'LOCATING'
  | 'DESIGNING'
  | 'VERIFYING'
  | 'WAITING_REVIEW'
  | 'PUBLISHING'
  | 'DONE'
  | 'NO_CHANGE'
  | 'REJECTED'
  | 'FAILED'

export interface TaskSummary {
  id: string
  title: string
  status: TaskStatus
  currentNode: string | null
  verifyRound: number
  createdAt: string
  updatedAt: string
}

export interface TaskDetail {
  task: TaskSummary
  errorMessage: string | null
  counts: { requirements: number; proposals: number }
}

/** AI가 고른 조회 (TOOL_PLAN). 이름은 화면 표시용, 모르면 null */
export interface Lookup {
  tool: 'get_article' | 'search_regulation'
  regulationId: string | null
  articleId: string | null
  query: string | null
  reason: string
  regulationTitle?: string | null
  articleTitle?: string | null
}

/** 진행 이벤트. type마다 payload가 다르다 (docs/BACKEND_API.md 5절) — 화면에서 쓰는 필드만 */
export interface TaskEvent {
  seq: number
  type: string
  createdAt: string
  payload: {
    node?: string
    tool?: string
    ok?: boolean
    summary?: string
    requirementId?: string
    lookups?: Lookup[]
    /** RESUMED: 확인 결정 (APPROVE · REJECT · REQUEST_CHANGES) */
    decision?: string
    proposalId?: string
  }
}

export interface Requirement {
  reqKey: string
  summary: string
  effectiveDate: string | null
  detail: {
    article: string
    title?: string | null
    change: 'modified' | 'added' | 'removed'
    old_value: string | null
    new_value: string | null
  }
}

export type Impact = 'change_required' | 'keep' | 'needs_check'

/** 수정 요청 결과 (docs/BACKEND_API.md 10.3). 수정 요청 전에는 none */
export type RevisionOutcome = 'none' | 'revised' | 'cannot_apply' | 'needs_clarification' | 'failed'

export interface Proposal {
  proposalKey: string
  reqKey: string
  productId: string
  productName: string
  ruleId: string | null
  impact: Impact
  unit: string | null
  currentValue: string | null
  proposedValue: string | null
  effectiveDate: string | null
  reason: string
  /** AI가 쓴 제안 문장 · 조치. 수정 요청은 이 글만 다시 쓴다 */
  proposed?: string | null
  followUp?: string[] | null
  /** 제안 버전. 수정 요청으로 다시 쓸 때마다 +1, 없으면 1 */
  version?: number
  revisionOutcome?: RevisionOutcome
  revisionResponse?: string | null
  /** 이전 버전과 달라진 칸 (recommendation · actions · optional_actions · precautions) */
  changedFields?: string[] | null
  verifyStatus: 'PENDING' | 'PASSED' | 'FAILED'
  latest: boolean
  decision: 'APPLY' | 'EXCLUDE' | null
  apply: { status: string; closedRuleId: string | null; newRuleId: string | null } | null
}

export interface ProposalsResponse {
  canApprove: boolean
  proposals: Proposal[]
}

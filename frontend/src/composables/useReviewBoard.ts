/**
 * 확인 화면의 사용자 선택: 뺄 제안(체크 해제) · 바꾸기로 확정한 제안 · 직접 고친 글(편집 초안).
 * 서버의 최종 결정은 한 번(APPROVE)이라 그 전까지는 화면이 들고 있다. 수정 요청 중에는 확인 화면이
 * 잠깐 사라지므로 컴포넌트 밖에 두고, 새로고침해도 이어지게 작업별로 sessionStorage에 남긴다.
 */
import { reactive, watch, type Ref } from 'vue'

import type { Draft } from '@/lib/revision'

interface Saved {
  excluded: string[]
  confirmed: string[]
  drafts: Record<string, Draft>
}

const storageKey = (taskId: string) => `dumbo.review.${taskId}`

function load(taskId: string): Saved | null {
  try {
    const raw = sessionStorage.getItem(storageKey(taskId))
    return raw ? (JSON.parse(raw) as Saved) : null
  } catch {
    return null
  }
}

export function useReviewBoard(taskId: Ref<string | undefined>) {
  const board = reactive({
    /** 체크를 푼 제안 (proposalKey). 반영에서 뺀다 */
    excluded: new Set<string>(),
    /** 바꾸기로 확정해 오른쪽 목록에 들어간 제안 */
    confirmed: new Set<string>(),
    /** 직접 고친 글. 바꾸기를 누르면 수정 요청으로 보낸다 */
    drafts: {} as Record<string, Draft>,
  })

  function restore(id: string | undefined) {
    const saved = id ? load(id) : null
    board.excluded = new Set(saved?.excluded)
    board.confirmed = new Set(saved?.confirmed)
    board.drafts = saved?.drafts ?? {}
  }

  watch(taskId, restore, { immediate: true })
  watch(
    () => [board.excluded.size, board.confirmed.size, JSON.stringify(board.drafts)],
    () => {
      const id = taskId.value
      if (!id) return
      const saved: Saved = {
        excluded: [...board.excluded],
        confirmed: [...board.confirmed],
        drafts: board.drafts,
      }
      try {
        sessionStorage.setItem(storageKey(id), JSON.stringify(saved))
      } catch {
        // 저장이 막힌 브라우저에서도 화면은 그대로 동작한다
      }
    },
  )

  return {
    board,
    toggle(keys: string[], on: boolean) {
      for (const key of keys) {
        if (on) board.excluded.delete(key)
        else {
          board.excluded.add(key)
          board.confirmed.delete(key)
        }
      }
    },
    confirm(keys: string[]) {
      for (const key of keys) {
        board.confirmed.add(key)
        delete board.drafts[key]
      }
    },
    unconfirm(key: string) {
      board.confirmed.delete(key)
    },
    saveDraft(key: string, draft: Draft) {
      board.drafts[key] = draft
      board.confirmed.delete(key)
    },
    discardDraft(key: string) {
      delete board.drafts[key]
    },
    /** 다시 쓴 제안(보고 고친 버전보다 새 버전)의 초안은 쓰였으니 지운다 */
    prune(versions: Map<string, number>) {
      for (const [key, draft] of Object.entries(board.drafts)) {
        const version = versions.get(key)
        if (version === undefined || version > draft.baseVersion) delete board.drafts[key]
      }
    },
    /** 초기화: 이 작업의 선택을 지운다 */
    forget() {
      const id = taskId.value
      if (id)
        try {
          sessionStorage.removeItem(storageKey(id))
        } catch {
          // 무시
        }
      restore(undefined)
    },
  }
}

export type ReviewBoard = ReturnType<typeof useReviewBoard>

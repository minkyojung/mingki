import { describe, expect, it } from 'vitest'

import { parseActions, revisionComment, sameDraft } from './revision'

const before = {
  proposed: 'DSR 한도를 35%로 낮추세요.',
  actions: ['심사 전산 변경', '안내 문구 수정'],
  baseVersion: 1,
}

describe('편집 → 수정 요청', () => {
  it('조치 칸은 한 줄에 하나, 앞의 글머리표는 뗀다', () => {
    expect(parseActions('- 심사 전산 변경\n\n• 안내 문구 수정 \n')).toEqual([
      '심사 전산 변경',
      '안내 문구 수정',
    ])
  })

  it('AI가 쓴 글과 같으면 고친 게 아니다 (앞뒤 공백은 무시)', () => {
    expect(sameDraft({ ...before, proposed: ' DSR 한도를 35%로 낮추세요. ' }, before)).toBe(true)
    expect(sameDraft({ ...before, actions: ['심사 전산 변경'] }, before)).toBe(false)
  })

  it('바뀐 칸만 고친 내용 그대로 적어 보낸다', () => {
    const comment = revisionComment(before, {
      ...before,
      actions: [...before.actions, '기존 계약 연장은 종전 기준 안내'],
    })
    expect(comment).toContain(
      '[조치]\n- 심사 전산 변경\n- 안내 문구 수정\n- 기존 계약 연장은 종전 기준 안내',
    )
    expect(comment).not.toContain('[제안 문장]')
    expect(comment).toContain('필수 값 · 시행일 · 적용 범위는 그대로')
  })
})

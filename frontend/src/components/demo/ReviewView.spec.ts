import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { defineComponent, h, ref } from 'vue'

import { useReviewBoard } from '@/composables/useReviewBoard'
import type { Briefing } from '@/lib/briefing'
import ReviewView from './ReviewView.vue'

const row = (productId: string) => ({
  key: `${productId}:DSR_LIMIT`,
  productId,
  productName: productId,
  change: '40% → 35%',
  reason: '',
  proposalId: `REQ-8:${productId}`,
  version: 1,
  proposed: 'DSR 한도를 35%로 낮추세요.',
  actions: ['심사 전산 변경'],
  revisionOutcome: 'none' as const,
  revisionResponse: null,
  changedFields: [],
  failed: false,
})

const briefing: Briefing = {
  productCount: 2,
  groups: [
    {
      reqKey: 'REQ-8',
      title: 'DSR 한도',
      before: '40%',
      after: '35%',
      added: false,
      rows: [row('P001'), row('P002')],
    },
  ],
  needsCheck: [],
  keep: [],
  changeKeys: ['P001:DSR_LIMIT', 'P002:DSR_LIMIT'],
  effectiveDate: '2026-12-01',
}

const log = { seconds: 1, requirementCount: 1, productCount: 2, lookups: [], allPassed: true }

function setup() {
  let review!: ReturnType<typeof useReviewBoard>
  const Host = defineComponent({
    setup() {
      review = useReviewBoard(ref('T1'))
      return () =>
        h(ReviewView, {
          briefing,
          log,
          review,
          revisionErrors: {},
          canApprove: true,
          busy: false,
          error: null,
        })
    },
  })
  const wrapper = mount(Host, { attachTo: document.body })
  return { wrapper, review: () => review }
}

describe('확인 화면', () => {
  it('편집해 저장하면 초안이 남고, 확인하기는 고치지 않은 것만 확인 완료로 · 고친 것은 수정 요청으로', async () => {
    sessionStorage.clear()
    const { wrapper, review } = setup()
    const triggers = wrapper.findAll('[data-slot=collapsible-trigger]')
    await triggers.find((t) => t.text().includes('P001'))!.trigger('click')
    await wrapper
      .findAll('button')
      .find((b) => b.text() === '편집')!
      .trigger('click')
    await wrapper.findAll('textarea')[1]!.setValue('심사 전산 변경\n안내 추가')
    await wrapper.find('form').trigger('submit')
    expect(Object.keys(review().board.drafts)).toEqual(['P001:DSR_LIMIT'])
    expect(wrapper.text()).toContain('편집함')

    await wrapper
      .findAll('button')
      .find((b) => b.text() === '확인하기')!
      .trigger('click')
    expect([...review().board.confirmed]).toEqual(['P002:DSR_LIMIT'])
    const revise = wrapper.findComponent(ReviewView).emitted('revise')
    expect(revise?.[0]?.[0]).toEqual([expect.objectContaining({ proposalId: 'REQ-8:P001' })])
    wrapper.unmount()
  })

  it('모두 확인 완료 목록에 들어가면 반영하기로 바뀌고, 누르면 바로 반영을 요청한다', async () => {
    sessionStorage.clear()
    const { wrapper } = setup()
    const button = (text: string) => wrapper.findAll('button').find((b) => b.text() === text)
    expect(button('반영하기')).toBeUndefined()
    await button('확인하기')!.trigger('click')
    expect(wrapper.text()).toContain('2 / 2')
    await button('반영하기')!.trigger('click')
    expect(wrapper.findComponent(ReviewView).emitted('approve')).toEqual([[[]]])
    wrapper.unmount()
  })
})

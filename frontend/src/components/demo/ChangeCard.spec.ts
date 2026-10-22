import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { ChangeGroup } from '@/lib/briefing'
import ChangeCard from './ChangeCard.vue'

const group: ChangeGroup = {
  reqKey: 'REQ-8',
  title: 'DSR 한도',
  before: '40%',
  after: '35%',
  added: false,
  rows: [
    {
      key: 'P001:DSR_LIMIT',
      productId: 'P001',
      productName: '리플 직장인 신용대출',
      change: '40% → 35%',
      reason: '지금 40%라 낮춰야 함',
      proposalId: 'REQ-8:P001',
      version: 1,
      proposed: 'DSR 한도를 35%로 낮추세요.',
      actions: ['심사 전산 변경'],
      revisionOutcome: 'none',
      revisionResponse: null,
      changedFields: [],
      failed: false,
    },
  ],
}

const props = { group, excluded: new Set<string>(), drafts: {}, errors: {}, busy: false }

describe('ChangeCard 편집', () => {
  it('고친 글을 저장하면 초안으로 올린다 (보고 고친 버전과 함께)', async () => {
    const wrapper = mount(ChangeCard, { props, attachTo: document.body })
    await wrapper.find('[data-slot=collapsible-trigger]').trigger('click')
    const edit = wrapper.findAll('button').find((b) => b.text().includes('편집'))!
    await edit.trigger('click')
    const [, actions] = wrapper.findAll('textarea')
    await actions!.setValue('심사 전산 변경\n기존 계약 연장은 종전 기준 안내')
    await wrapper.find('form').trigger('submit')
    expect(wrapper.emitted('save')).toEqual([
      [
        'P001:DSR_LIMIT',
        {
          proposed: 'DSR 한도를 35%로 낮추세요.',
          actions: ['심사 전산 변경', '기존 계약 연장은 종전 기준 안내'],
          baseVersion: 1,
        },
      ],
    ])
    wrapper.unmount()
  })

  it('AI가 쓴 글 그대로 저장하면 초안을 만들지 않는다', async () => {
    const wrapper = mount(ChangeCard, { props, attachTo: document.body })
    await wrapper.find('[data-slot=collapsible-trigger]').trigger('click')
    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('편집'))!
      .trigger('click')
    await wrapper.find('form').trigger('submit')
    expect(wrapper.emitted('save')).toBeUndefined()
    expect(wrapper.emitted('discard')).toEqual([['P001:DSR_LIMIT']])
    wrapper.unmount()
  })
})

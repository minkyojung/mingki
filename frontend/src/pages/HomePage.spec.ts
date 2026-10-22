import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import HomePage from './HomePage.vue'

vi.mock('@/api/tasks', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/tasks')>()),
  login: vi.fn(),
  latestTask: vi.fn().mockResolvedValue(null),
}))

describe('HomePage', () => {
  it('진행 중인 작업이 없으면 시작 화면 (규정 이름 · 시행일은 올릴 원문에서)', async () => {
    const wrapper = mount(HomePage)
    await flushPromises()
    expect(wrapper.text()).toContain('Dumbo')
    expect(wrapper.text()).toContain('개인신용대출 운영기준이바뀌었어요')
    expect(wrapper.text()).toContain('v3 → v4 · 12월 1일 시행')
    expect(wrapper.find('button:not([disabled])').exists()).toBe(true)
    expect(wrapper.text()).toContain('영향받는 상품 찾기')
  })
})

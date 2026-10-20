<script setup lang="ts">
import { computed, ref } from 'vue'

const amount = ref(0)
const termMonths = ref(12)
const annualIncome = ref(0)
const agreed = ref(false)

const canSubmit = computed(() => amount.value > 0 && annualIncome.value > 0 && agreed.value)

function submit() {
  // 신청 API 호출
}
</script>

<template>
  <section class="loan-apply">
    <h1>신용대출 신청</h1>

    <form @submit.prevent="submit">
      <label>
        신청 금액
        <input v-model.number="amount" type="number" min="0" />
      </label>

      <label>
        대출 기간
        <select v-model.number="termMonths">
          <option :value="12">12개월</option>
          <option :value="24">24개월</option>
          <option :value="36">36개월</option>
          <option :value="60">60개월</option>
        </select>
      </label>

      <label>
        연소득
        <input v-model.number="annualIncome" type="number" min="0" />
      </label>

      <label class="agree">
        <input v-model="agreed" type="checkbox" />
        상품 설명을 확인했습니다
      </label>

      <p class="dsr-notice">
        <strong>안내</strong>
        DSR 40% 이하만 신청 가능합니다
      </p>

      <button type="submit" :disabled="!canSubmit">신청하기</button>
    </form>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { RouterView } from 'vue-router'

import SplashView from '@/components/demo/SplashView.vue'

// 처음 들어올 때만 스플래시. 버튼을 누른 뒤에야 홈이 마운트된다
const entered = ref(false)
</script>

<template>
  <Transition mode="out-in" name="fade">
    <SplashView v-if="!entered" @enter="entered = true" />
    <RouterView v-else />
  </Transition>
</template>

<style>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 250ms ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>

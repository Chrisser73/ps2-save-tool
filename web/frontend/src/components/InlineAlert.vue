<script setup>
defineProps({ message: { type: String, default: '' } })
const emit = defineEmits(['close'])
</script>

<template>
  <div v-if="message" class="mt-3 flex items-start justify-between gap-3 rounded-xl border border-red-500/30 bg-red-500/10 px-3.5 py-3 text-sm text-red-200">
    <span class="min-w-0 break-words">{{ message }}</span>
    <button type="button" class="shrink-0 text-lg leading-none text-red-300/60 hover:text-white" aria-label="Dismiss error" @click="emit('close')">×</button>
  </div>
</template>

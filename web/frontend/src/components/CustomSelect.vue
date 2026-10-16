<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  modelValue: [String, Number],
  options: { type: Array, default: () => [] },
  disabled: Boolean,
})
const emit = defineEmits(['update:modelValue'])
const open = ref(false)
const normalized = computed(() => props.options.map(option => typeof option === 'object' ? option : ({ label: String(option), value: option })))
const current = computed(() => normalized.value.find(option => option.value === props.modelValue) ?? normalized.value[0])

function choose(value) {
  emit('update:modelValue', value)
  open.value = false
}
</script>

<template>
  <div class="relative" @keydown.esc="open = false">
    <button
      type="button"
      class="flex w-full items-center justify-between gap-3 rounded-xl border border-white/10 bg-select px-3 py-2.5 text-left text-sm text-slate-100 outline-none transition hover:border-ps2-500/40 focus:border-ps2-500/60 disabled:cursor-not-allowed disabled:opacity-50"
      :disabled="disabled"
      @click="open = !open"
    >
      <span class="truncate">{{ current?.label ?? 'Select…' }}</span>
      <svg class="size-4 shrink-0 text-slate-500 transition" :class="open && 'rotate-180'" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.51a.75.75 0 01-1.08 0l-4.25-4.51a.75.75 0 01.02-1.06z" clip-rule="evenodd"/></svg>
    </button>
    <div v-if="open" class="absolute z-50 mt-1.5 max-h-64 w-full overflow-auto rounded-xl border border-ps2-500/25 bg-select-menu p-1.5 shadow-2xl shadow-black/60 ring-1 ring-black/40">
      <button
        v-for="option in normalized"
        :key="String(option.value)"
        type="button"
        class="flex w-full items-center rounded-lg px-3 py-2 text-left text-sm transition"
        :class="option.value === modelValue ? 'bg-ps2-600 text-white' : 'text-slate-300 hover:bg-white/[0.06] hover:text-white'"
        @click="choose(option.value)"
      >{{ option.label }}</button>
    </div>
    <button v-if="open" type="button" class="fixed inset-0 z-40 cursor-default" aria-label="Close select" @click="open = false"></button>
  </div>
</template>

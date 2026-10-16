<script setup>
defineProps({
  label: { type: String, default: 'Choose file' },
  hint: { type: String, default: 'Click or drop a file' },
  modelValue: { type: Object, default: null },
  accept: { type: String, default: '' },
  disabled: Boolean,
})
const emit = defineEmits(['update:modelValue'])

function choose(event) {
  const file = event.target.files?.[0]
  if (file) emit('update:modelValue', file)
  event.target.value = ''
}
function drop(event) {
  const file = event.dataTransfer?.files?.[0]
  if (file) emit('update:modelValue', file)
}
</script>

<template>
  <label
    class="group block rounded-xl border border-dashed border-white/12 bg-black/20 px-3.5 py-3 transition hover:border-ps2-500/45 hover:bg-ps2-500/[0.035]"
    :class="disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer'"
    @dragover.prevent
    @drop.prevent="!disabled && drop($event)"
  >
    <input class="hidden" type="file" :accept="accept" :disabled="disabled" @change="choose" />
    <div class="flex items-center gap-3">
      <div class="grid size-8 shrink-0 place-items-center rounded-lg bg-ps2-500/10 text-ps2-300 ring-1 ring-inset ring-ps2-500/20">
        <svg viewBox="0 0 24 24" class="size-4" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 16V5m0 0L8.5 8.5M12 5l3.5 3.5M5 14.5V18a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3.5"/></svg>
      </div>
      <div class="min-w-0">
        <div class="text-xs font-medium text-slate-200">{{ label }}</div>
        <div class="mt-0.5 truncate text-[11px] text-slate-600">{{ modelValue?.name || hint }}</div>
      </div>
    </div>
  </label>
</template>

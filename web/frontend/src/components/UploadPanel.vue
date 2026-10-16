<script setup>
import SpinnerIcon from './SpinnerIcon.vue'

defineProps({ busy: Boolean })
const emit = defineEmits(['file'])

function choose(event) {
  const file = event.target.files?.[0]
  if (file) emit('file', file)
  event.target.value = ''
}
function drop(event) {
  const file = event.dataTransfer?.files?.[0]
  if (file) emit('file', file)
}
</script>

<template>
  <label
    class="group block rounded-2xl border border-dashed border-white/15 bg-white/[0.025] p-7 text-center transition hover:border-ps2-500/60 hover:bg-ps2-500/[0.04]"
    :class="busy ? 'cursor-wait opacity-75' : 'cursor-pointer'"
    @dragover.prevent
    @drop.prevent="!busy && drop($event)"
  >
    <input class="hidden" type="file" :disabled="busy" @change="choose" />
    <div class="mx-auto mb-3 grid size-12 place-items-center rounded-xl bg-ps2-500/10 text-ps2-300 ring-1 ring-inset ring-ps2-500/20">
      <SpinnerIcon v-if="busy" class="size-6" />
      <svg v-else viewBox="0 0 24 24" class="size-6" fill="none" stroke="currentColor" stroke-width="1.8">
        <path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M5 14v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4"/>
      </svg>
    </div>
    <p class="font-medium text-white">{{ busy ? 'Reading save…' : 'Choose a save or memory card' }}</p>
    <p class="mt-1 text-sm text-slate-400">PSV, PSU, PS1 saves and PS1/PS2 memory-card images</p>
    <p class="mt-3 text-xs text-slate-600">Click to browse · drag & drop also works</p>
  </label>
</template>

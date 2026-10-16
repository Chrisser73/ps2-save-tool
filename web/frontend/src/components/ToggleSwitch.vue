<script setup>
defineProps({
  modelValue: Boolean,
  label: { type: String, default: "" },
  disabled: Boolean,
});
const emit = defineEmits(["update:modelValue"]);
</script>

<template>
  <button
    type="button"
    role="switch"
    :aria-checked="modelValue"
    :disabled="disabled"
    class="inline-flex items-center gap-2.5 px-3 py-2 text-xs text-slate-400 transition hover:border-ps2-500/30 hover:text-slate-200 disabled:opacity-40"
    @click="emit('update:modelValue', !modelValue)"
  >
    <span
      class="relative h-5 w-9 shrink-0 rounded-full border transition"
      :class="
        modelValue
          ? 'border-ps2-500/60 bg-ps2-600/80'
          : 'border-white/10 bg-black/35'
      "
    >
      <span
        class="absolute top-1/2 size-3.5 -translate-y-1/2 rounded-full bg-white shadow transition-all"
        :class="modelValue ? 'left-[18px]' : 'left-[2px]'"
      />
    </span>
    <span>{{ label }}</span>
  </button>
</template>

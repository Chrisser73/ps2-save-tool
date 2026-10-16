<script setup>defineProps({ label: String, value: [String, Number, Boolean] })</script>
<template>
  <div class="min-w-0 rounded-xl border border-white/8 bg-white/[0.025] px-3 py-2.5">
    <div class="text-[10px] font-semibold uppercase tracking-[.16em] text-slate-500">{{ label }}</div>
    <div class="mt-1 truncate text-sm text-slate-200">{{ value ?? '—' }}</div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import LogoMark from './components/LogoMark.vue'
import UploadPanel from './components/UploadPanel.vue'
import InfoBadge from './components/InfoBadge.vue'
import CustomSelect from './components/CustomSelect.vue'
import SpinnerIcon from './components/SpinnerIcon.vue'
import InlineAlert from './components/InlineAlert.vue'
import ToggleSwitch from './components/ToggleSwitch.vue'
import MiniFileDrop from './components/MiniFileDrop.vue'

const upload = ref(null)
const busy = ref(false)
const activeAction = ref('')
const errors = ref({})
const previewMode = ref('rendered')
const previewRevision = ref(0)
const selectedSave = ref(null)
const selectedSaveDetails = ref(null)
const selectedFiles = ref([])
const filterText = ref('')
const outputFormat = ref('')
const latestValidationLog = ref('')
const validationLogOpen = ref(false)
const toasts = ref([])
let toastId = 0

const toolsOpen = ref(false)
const toolTab = ref('tools')
const mergeSource = ref(null)
const mergeDestination = ref(null)
const injectTarget = ref(null)
const injectModified = ref(null)

const createSystem = ref('ps2')
const createSize = ref(8)
const createFormat = ref('ps2')

const metadataOpen = ref(false)
const metadataDir = ref('')
const metadataTitle = ref('')
const autoBackup = ref(true)

const batchTarget = ref('PSV')
const batchInput = ref(null)
const replaceInput = ref(null)
const addInput = ref(null)

const confirmState = ref(null)

const details = computed(() => upload.value?.details ?? null)
const fileId = computed(() => upload.value?.id)
const isPS2Card = computed(() => details.value?.kind === 'ps2-card')
const isPS1Card = computed(() => details.value?.kind === 'ps1-card')
const isCard = computed(() => isPS2Card.value || isPS1Card.value)
const usagePercent = computed(() => {
  if (!isPS2Card.value || !details.value?.size) return 0
  return Math.max(0, Math.min(100, Number(details.value.used || 0) / Number(details.value.size) * 100))
})

function normalizeSearch(value) {
  return String(value ?? '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[’‘]/g, "'")
    .toLocaleLowerCase()
    .replace(/\s+/g, ' ')
    .trim()
}

const filteredSaves = computed(() => {
  const saves = details.value?.saves ?? []
  const tokens = normalizeSearch(filterText.value).split(' ').filter(Boolean)
  if (!tokens.length) return saves
  return saves.filter(save => {
    const haystack = normalizeSearch([
      save.title,
      save.dirname,
      save.filename,
      save.productCode,
      save.region,
      save.identifier,
    ].filter(Boolean).join(' '))
    return tokens.every(token => haystack.includes(token))
  })
})

const selectedCardSave = computed(() => {
  if (!details.value?.saves?.length) return null
  const key = selectedSave.value
  if (isPS2Card.value) return details.value.saves.find(s => s.dirname === key) ?? null
  return details.value.saves.find(s => Number(s.slot) === Number(key)) ?? null
})
const targets = computed(() => details.value?.convertTargets ?? (isPS2Card.value ? ['PSV','PSU','MAX'] : isPS1Card.value ? ['PSV','MCS','PSX','RAW'] : []))
const targetOptions = computed(() => targets.value.map(value => ({ label: value, value })))
const createSizeOptions = computed(() => createSystem.value === 'ps1'
  ? [{ label: '128 KB (15 save blocks)', value: 0 }]
  : [8, 16, 32, 64].map(value => ({ label: `${value} MB`, value })))

function humanSize(n) {
  if (n == null) return '—'
  const units = ['B','KB','MB','GB']
  let value = Number(n), i = 0
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i++ }
  return i === 0 ? `${Math.round(value)} ${units[i]}` : `${value.toFixed(1)} ${units[i]}`
}

function setError(scope, value) {
  errors.value = { ...errors.value, [scope]: value instanceof Error ? value.message : String(value || '') }
}
function clearError(scope) {
  if (!errors.value[scope]) return
  const next = { ...errors.value }
  delete next[scope]
  errors.value = next
}
function errorFor(scope) { return errors.value[scope] || '' }
function isAction(name) { return activeAction.value === name }

function pushToast(text, tone = 'success', timeout = 2500) {
  const id = ++toastId
  toasts.value = [...toasts.value, { id, text, tone }]
  window.setTimeout(() => { toasts.value = toasts.value.filter(item => item.id !== id) }, timeout)
}

async function runAction(name, scope, fn) {
  clearError(scope)
  activeAction.value = name
  try { return await fn() }
  catch (e) { setError(scope, e); return null }
  finally { if (activeAction.value === name) activeAction.value = '' }
}

function previewUrl() {
  if (!fileId.value) return ''
  const qs = new URLSearchParams({ mode: previewMode.value, r: String(previewRevision.value) })
  if (isPS2Card.value && selectedCardSave.value) qs.set('save', selectedCardSave.value.dirname)
  if (isPS1Card.value && selectedCardSave.value) qs.set('slot', selectedCardSave.value.slot)
  return `/api/files/${fileId.value}/preview?${qs}`
}

async function jsonRequest(url, options = {}) {
  const response = await fetch(url, options)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.detail || 'Operation failed')
  return payload
}

async function onFile(file) {
  busy.value = true
  clearError('upload')
  latestValidationLog.value = ''
  upload.value = null
  selectedSaveDetails.value = null
  selectedFiles.value = []
  try {
    const body = new FormData(); body.append('file', file)
    const payload = await jsonRequest('/api/files/upload', { method: 'POST', body })
    upload.value = payload
    const saves = payload.details?.saves || []
    selectedSave.value = payload.details?.kind === 'ps2-card' ? saves[0]?.dirname : payload.details?.kind === 'ps1-card' ? saves[0]?.slot : null
    outputFormat.value = payload.details?.convertTargets?.[0] || (payload.details?.kind === 'ps2-card' ? 'PSV' : '')
    filterText.value = ''
  } catch (e) { setError('upload', e) }
  finally { busy.value = false }
}

async function download(url, options = {}) {
  const response = await fetch(url, options)
  if (!response.ok) {
    let message = 'Operation failed'
    try { message = (await response.json()).detail || message } catch {}
    throw new Error(message)
  }
  const blob = await response.blob()
  const disposition = response.headers.get('content-disposition') || ''
  const match = disposition.match(/filename="?([^";]+)"?/i)
  const name = match?.[1] || 'download.bin'
  const href = URL.createObjectURL(blob)
  const a = document.createElement('a'); a.href = href; a.download = name; a.click()
  window.setTimeout(() => URL.revokeObjectURL(href), 1000)
}

async function loadSelectedSaveDetails() {
  selectedFiles.value = []
  metadataOpen.value = false
  clearError('selected-save')
  if (!fileId.value) { selectedSaveDetails.value = null; return }
  if (!isCard.value) {
    if (details.value?.kind === 'ps2-save') {
      selectedSaveDetails.value = { ...details.value, fileCount: details.value.files?.length ?? 0 }
      syncMetadata()
    }
    return
  }
  if (!selectedCardSave.value) { selectedSaveDetails.value = null; return }
  try {
    const qs = new URLSearchParams()
    if (isPS2Card.value) qs.set('save', selectedCardSave.value.dirname)
    else qs.set('slot', selectedCardSave.value.slot)
    selectedSaveDetails.value = await jsonRequest(`/api/files/${fileId.value}/save-details?${qs}`)
    syncMetadata()
  } catch (e) { setError('selected-save', e); selectedSaveDetails.value = null }
}

function syncMetadata() {
  const d = selectedSaveDetails.value
  metadataDir.value = d?.dirname || ''
  metadataTitle.value = (d?.title || '').replace(' / ', '\n')
}

watch([selectedSave, fileId], loadSelectedSaveDetails)
watch(createSystem, value => {
  createSize.value = value === 'ps1' ? 0 : 8
  createFormat.value = value === 'ps1' ? 'ps1' : 'ps2'
})
watch(targets, values => { if (values?.length && !values.includes(outputFormat.value)) outputFormat.value = values[0] })

async function convert() {
  await runAction('export', 'export', async () => {
    const body = new FormData(); body.append('target', outputFormat.value)
    if (isPS2Card.value && selectedCardSave.value) body.append('save', selectedCardSave.value.dirname)
    if (isPS1Card.value && selectedCardSave.value) body.append('save', selectedCardSave.value.slot)
    await download(`/api/files/${fileId.value}/convert`, { method: 'POST', body })
    pushToast(`${outputFormat.value} export started.`)
  })
}

async function exportFolder() {
  await runAction('export-folder', isPS2Card.value ? 'files' : 'export', async () => {
    const qs = new URLSearchParams()
    if (isPS2Card.value && selectedCardSave.value) qs.set('save', selectedCardSave.value.dirname)
    await download(`/api/files/${fileId.value}/export-folder?${qs}`)
    pushToast('Save folder export started.')
  })
}

async function validate() {
  await runAction('validate', 'card-actions', async () => {
    const p = await jsonRequest(`/api/files/${fileId.value}/validate`)
    latestValidationLog.value = p.message || 'Validation completed.'
    pushToast('Card validation completed.', 'success', 2500)
  })
}

async function mergeCards() {
  if (!mergeSource.value || !mergeDestination.value) { setError('merge', 'Choose both PS2 memory cards first.'); return }
  await runAction('merge', 'merge', async () => {
    const body = new FormData(); body.append('source', mergeSource.value); body.append('destination', mergeDestination.value)
    await download('/api/cards/merge', { method: 'POST', body })
    pushToast('Merged card download started.')
  })
}

async function injectPsv() {
  if (!injectTarget.value || !injectModified.value) { setError('inject', 'Choose the target PSV and modified save first.'); return }
  await runAction('inject', 'inject', async () => {
    const body = new FormData(); body.append('target', injectTarget.value); body.append('modified', injectModified.value)
    await download('/api/ps2/inject', { method: 'POST', body })
    pushToast('Compatible save injected and re-signed.')
  })
}

async function createCard() {
  await runAction('create-card', 'create-card', async () => {
    const body = new FormData()
    const format = createSystem.value === 'ps1' ? 'ps1' : createFormat.value
    body.append('size_mb', createSystem.value === 'ps1' ? 0 : createSize.value)
    body.append('format', format)
    await download('/api/cards/create', { method: 'POST', body })
    pushToast('Memory card created.')
  })
}

function appendBackupFlag(body) {
  body.append('auto_backup', autoBackup.value ? 'true' : 'false')
}

async function saveMetadata() {
  await runAction('metadata', 'metadata', async () => {
    const body = new FormData(); body.append('dirname', metadataDir.value); body.append('title', metadataTitle.value); appendBackupFlag(body)
    if (isPS2Card.value && selectedCardSave.value) body.append('save', selectedCardSave.value.dirname)
    const payload = await jsonRequest(`/api/files/${fileId.value}/metadata`, { method: 'POST', body })
    upload.value.details = payload.details
    selectedSave.value = payload.selected || metadataDir.value
    previewRevision.value++
    await loadSelectedSaveDetails()
    metadataOpen.value = false
    pushToast('Metadata updated.')
  })
}

function toggleFile(name) {
  selectedFiles.value = selectedFiles.value.includes(name)
    ? selectedFiles.value.filter(item => item !== name)
    : [...selectedFiles.value, name]
}

async function extractFiles() {
  if (!selectedFiles.value.length) { setError('files', 'Select one or more files first.'); return }
  await runAction('extract-files', 'files', async () => {
    const body = new FormData(); body.append('names_json', JSON.stringify(selectedFiles.value))
    if (isPS2Card.value) body.append('save', selectedCardSave.value.dirname)
    await download(`/api/files/${fileId.value}/files/extract`, { method: 'POST', body })
  })
}

function chooseReplaceFile() {
  if (selectedFiles.value.length !== 1) { setError('files', 'Select exactly one file to replace.'); return }
  replaceInput.value?.click()
}
async function replaceFile(event) {
  const file = event.target.files?.[0]; event.target.value = ''
  if (!file) return
  await runAction('replace-file', 'files', async () => {
    const body = new FormData(); body.append('save', isPS2Card.value ? selectedCardSave.value.dirname : String(selectedCardSave.value.slot)); body.append('name', selectedFiles.value[0]); body.append('file', file); appendBackupFlag(body)
    const payload = await jsonRequest(`/api/files/${fileId.value}/files/replace`, { method: 'POST', body })
    upload.value.details = payload.details; selectedSaveDetails.value = payload.saveDetails; selectedFiles.value = []
    previewRevision.value++; pushToast('File replaced.')
  })
}

async function addFile(event) {
  const file = event.target.files?.[0]; event.target.value = ''
  if (!file) return
  await runAction('add-file', 'files', async () => {
    const body = new FormData(); body.append('save', selectedCardSave.value.dirname); body.append('file', file); appendBackupFlag(body)
    const payload = await jsonRequest(`/api/files/${fileId.value}/files/add`, { method: 'POST', body })
    upload.value.details = payload.details; selectedSaveDetails.value = payload.saveDetails
    previewRevision.value++; pushToast('File added.')
  })
}

function requestDeleteFiles() {
  if (!selectedFiles.value.length) { setError('files', 'Select one or more files first.'); return }
  confirmState.value = {
    title: 'Delete save file(s)?',
    text: `Sure? Delete ${selectedFiles.value.length} selected file(s) from this save?`,
    confirmLabel: 'Yes, delete',
    action: deleteFiles,
  }
}
async function deleteFiles() {
  confirmState.value = null
  await runAction('delete-files', 'files', async () => {
    const body = new FormData(); body.append('save', selectedCardSave.value.dirname); body.append('names_json', JSON.stringify(selectedFiles.value)); appendBackupFlag(body)
    const payload = await jsonRequest(`/api/files/${fileId.value}/files/delete`, { method: 'POST', body })
    upload.value.details = payload.details; selectedSaveDetails.value = payload.saveDetails; selectedFiles.value = []
    previewRevision.value++; pushToast('Selected file(s) deleted.')
  })
}

function requestDeleteSave() {
  if (!selectedCardSave.value) return
  confirmState.value = {
    title: 'Delete Save?',
    text: `Sure? Delete “${selectedCardSave.value.title}” (${selectedCardSave.value.dirname || selectedCardSave.value.filename || `slot ${selectedCardSave.value.slot}`}) from this memory card?`,
    confirmLabel: 'Yes, delete',
    action: deleteSave,
  }
}
async function deleteSave() {
  confirmState.value = null
  await runAction('delete-save', 'selected-save', async () => {
    const body = new FormData(); body.append('save', isPS2Card.value ? selectedCardSave.value.dirname : String(selectedCardSave.value.slot)); appendBackupFlag(body)
    const payload = await jsonRequest(`/api/files/${fileId.value}/delete-save`, { method: 'POST', body })
    upload.value.details = payload.details
    selectedSave.value = isPS2Card.value ? (payload.details.saves?.[0]?.dirname ?? null) : (payload.details.saves?.[0]?.slot ?? null)
    selectedSaveDetails.value = null; previewRevision.value++
    pushToast('Save deleted from the temporary card copy.')
  })
}

async function batchImport(event) {
  const files = [...(event.target.files || [])]; event.target.value = ''
  if (!files.length) return
  await runAction('batch-import', 'card-actions', async () => {
    const body = new FormData(); files.forEach(file => body.append('files', file)); appendBackupFlag(body)
    const payload = await jsonRequest(`/api/files/${fileId.value}/card/import`, { method: 'POST', body })
    upload.value.details = payload.details
    selectedSave.value = payload.details.saves?.[0]?.dirname ?? null
    previewRevision.value++
    pushToast(`Imported ${payload.imported} save(s).`)
  })
}

async function batchExport() {
  await runAction('batch-export', 'card-actions', async () => {
    await download(`/api/files/${fileId.value}/batch-export?target=${encodeURIComponent(batchTarget.value)}`)
    pushToast(`${batchTarget.value} batch export started.`)
  })
}

async function downloadCard() {
  await runAction('download-card', 'card-actions', async () => {
    await download(`/api/files/${fileId.value}/download`)
  })
}

async function restoreBackup() {
  await runAction('restore-backup', 'card-actions', async () => {
    const payload = await jsonRequest(`/api/files/${fileId.value}/restore-backup`, { method: 'POST' })
    upload.value.details = payload.details
    selectedSave.value = payload.details.saves?.[0]?.dirname ?? null
    previewRevision.value++
    pushToast('Latest automatic backup restored.')
  })
}

async function repairCard() {
  await runAction('repair', 'experimental', async () => {
    await download(`/api/files/${fileId.value}/repair`)
    pushToast('Rebuilt card download started.')
  })
}
</script>

<template>
  <div class="ps2-grid min-h-screen bg-app text-slate-200">
    <div class="pointer-events-none fixed inset-x-0 top-0 h-80 bg-[radial-gradient(circle_at_50%_-15%,rgba(51,117,255,.18),transparent_58%)]"></div>

    <header class="relative border-b border-white/7 bg-black/30 backdrop-blur-xl">
      <div class="mx-auto flex max-w-7xl items-center gap-4 px-4 py-4 sm:px-6 lg:px-8">
        <LogoMark />
        <div>
          <h1 class="font-semibold tracking-tight text-white">PS2 Save Tool <span class="text-ps2-400">Web</span></h1>
          <p class="text-xs text-slate-500">PS1 / PS2 save converter & memory card manager</p>
        </div>
      </div>
    </header>

    <main class="relative mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
      <div class="mb-5 flex justify-center">
        <button class="rounded-xl border border-white/10 bg-white/[0.035] px-4 py-2.5 text-sm text-slate-300 transition hover:border-ps2-500/40 hover:text-white" @click="toolsOpen = !toolsOpen">Memory Card Tools</button>
      </div>

      <div v-if="toolsOpen" class="mb-5 overflow-hidden rounded-2xl border border-ps2-500/20 bg-ps2-950/20 glow">
        <div class="flex gap-1 border-b border-white/8 p-2">
          <button class="rounded-lg px-3 py-2 text-sm" :class="toolTab === 'tools' ? 'bg-ps2-600 text-white' : 'text-slate-400 hover:bg-white/5'" @click="toolTab = 'tools'">Tools</button>
          <button class="rounded-lg px-3 py-2 text-sm" :class="toolTab === 'experimental' ? 'bg-amber-500/15 text-amber-200 ring-1 ring-inset ring-amber-500/20' : 'text-slate-400 hover:bg-white/5'" @click="toolTab = 'experimental'">Experimental</button>
        </div>

        <div v-if="toolTab === 'tools'" class="grid gap-4 p-4 lg:grid-cols-3">
          <section class="min-w-0 rounded-xl border border-white/8 bg-black/15 p-4">
            <h2 class="font-medium text-white">Create Memory Card</h2>
            <p class="mt-1 text-xs leading-5 text-slate-500">Create a fresh PS1 or PS2 memory card image.</p>
            <div class="mt-3 space-y-2.5">
              <label class="block text-[11px] font-semibold uppercase tracking-[.12em] text-slate-500">System</label>
              <CustomSelect v-model="createSystem" :options="[{label:'PlayStation 2',value:'ps2'},{label:'PlayStation 1',value:'ps1'}]" />
              <label class="block text-[11px] font-semibold uppercase tracking-[.12em] text-slate-500">Card size</label>
              <CustomSelect v-model="createSize" :options="createSizeOptions" />
              <template v-if="createSystem === 'ps2'">
                <label class="block text-[11px] font-semibold uppercase tracking-[.12em] text-slate-500">Image type</label>
                <CustomSelect v-model="createFormat" :options="[{label:'PCSX2 / PS2 image (.ps2)',value:'ps2'},{label:'Raw image without ECC (.mc2)',value:'mc2'}]" />
              </template>
              <button class="spinner-button w-full rounded-xl bg-ps2-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-ps2-500 disabled:opacity-50" :disabled="!!activeAction" @click="createCard"><SpinnerIcon v-if="isAction('create-card')" />{{ isAction('create-card') ? 'Creating…' : 'Create' }}</button>
              <InlineAlert :message="errorFor('create-card')" @close="clearError('create-card')" />
            </div>
          </section>

          <section class="min-w-0 rounded-xl border border-white/8 bg-black/15 p-4">
            <h2 class="font-medium text-white">Merge Cards</h2>
            <p class="mt-1 text-xs leading-5 text-slate-500">Copy non-duplicate saves from Card A into a copy of Card B.</p>
            <div class="mt-3 space-y-2.5">
              <MiniFileDrop v-model="mergeSource" label="Card A" hint="Choose or drop a PS2 memory card" accept=".ps2,.mc2" :disabled="!!activeAction" />
              <MiniFileDrop v-model="mergeDestination" label="Card B" hint="Choose or drop the destination card" accept=".ps2,.mc2" :disabled="!!activeAction" />
              <button class="spinner-button w-full rounded-xl bg-ps2-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-ps2-500 disabled:opacity-50" :disabled="!!activeAction" @click="mergeCards"><SpinnerIcon v-if="isAction('merge')" />{{ isAction('merge') ? 'Merging…' : 'Merge' }}</button>
              <InlineAlert :message="errorFor('merge')" @close="clearError('merge')" />
            </div>
          </section>

          <section class="min-w-0 rounded-xl border border-white/8 bg-black/15 p-4">
            <h2 class="font-medium text-white">PS3 PSV Injector</h2>
            <p class="mt-1 text-xs leading-5 text-slate-500">Inject a modified PS2 save into a compatible PS3 PSV container.</p>
            <div class="mt-3 space-y-2.5">
              <MiniFileDrop v-model="injectTarget" label="Target .PSV" hint="Choose or drop the PS3 PSV" accept=".psv" :disabled="!!activeAction" />
              <MiniFileDrop v-model="injectModified" label="Modified save" hint="Choose or drop PSV / PSU / MAX / CBS / XPS / SPS" accept=".psv,.psu,.max,.cbs,.xps,.sps" :disabled="!!activeAction" />
              <button class="spinner-button w-full rounded-xl bg-ps2-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-ps2-500 disabled:opacity-50" :disabled="!!activeAction" @click="injectPsv"><SpinnerIcon v-if="isAction('inject')" />{{ isAction('inject') ? 'Injecting…' : 'Inject' }}</button>
              <p class="mt-1 text-xs leading-5 text-slate-500">Checks the save ID and compatibility, injects the payload, re-signs the PSV, then downloads the result.</p>
              <InlineAlert :message="errorFor('inject')" @close="clearError('inject')" />
            </div>
          </section>
        </div>

        <div v-else class="p-4">
          <section class="max-w-2xl rounded-xl border border-amber-500/20 bg-amber-500/[0.04] p-4">
            <div class="text-xs font-semibold uppercase tracking-[.16em] text-amber-300">Experimental</div>
            <h2 class="mt-1 font-medium text-white">Rebuild / Repair PS2 Card</h2>
            <p class="mt-2 text-sm leading-6 text-slate-400">Creates a new card image and copies every readable save into it. The source is never overwritten.</p>
            <button class="spinner-button mt-4 rounded-xl bg-amber-500/15 px-4 py-2.5 text-sm font-medium text-amber-100 ring-1 ring-inset ring-amber-500/25 hover:bg-amber-500/20 disabled:opacity-40" :disabled="!isPS2Card || !!activeAction" @click="repairCard"><SpinnerIcon v-if="isAction('repair')" />{{ isAction('repair') ? 'Rebuilding…' : 'Rebuild Card' }}</button>
            <p v-if="!isPS2Card" class="mt-2 text-xs text-slate-600">Open a PS2 memory card first.</p>
            <InlineAlert :message="errorFor('experimental')" @close="clearError('experimental')" />
          </section>
        </div>
      </div>

      <div v-if="!upload" class="mx-auto max-w-3xl pt-3 sm:pt-10">
        <div class="mb-8 text-center">
          <div class="mb-3 text-xs font-semibold uppercase tracking-[.24em] text-ps2-400">PS2 save management tool</div>
          <h2 class="text-3xl font-semibold tracking-tight text-white sm:text-4xl">Inspect, manage and convert your saves.</h2>
          <p class="mx-auto mt-3 max-w-2xl text-sm leading-6 text-slate-400 sm:text-base">A focused browser-based workspace for PS1 and PS2 save conversion, memory-card management, icon previews and PS3 PSV workflows.</p>
        </div>
        <UploadPanel :busy="busy" @file="onFile" />
        <InlineAlert :message="errorFor('upload')" @close="clearError('upload')" />
      </div>

      <template v-else>
        <div class="mb-5 flex flex-col gap-3 rounded-2xl border border-white/8 bg-white/[0.025] p-4 sm:flex-row sm:items-center sm:justify-between">
          <div class="min-w-0"><div class="text-xs uppercase tracking-[.18em] text-ps2-400">Loaded</div><div class="truncate font-medium text-white">{{ upload.name }}</div></div>
          <button class="shrink-0 rounded-xl border border-white/10 bg-white/[0.04] px-4 py-2 text-sm hover:border-ps2-500/40" @click="upload = null">Open another file</button>
        </div>

        <div class="grid min-w-0 gap-5 lg:grid-cols-[minmax(0,1.45fr)_minmax(330px,.8fr)]">
          <section class="min-w-0 space-y-5">
            <div class="min-w-0 rounded-2xl border border-white/8 bg-surface/90 p-4 sm:p-5 glow">
              <div class="mb-4 flex min-w-0 items-start justify-between gap-4">
                <div class="min-w-0"><p class="text-xs font-semibold uppercase tracking-[.18em] text-ps2-400">{{ details.kind.replaceAll('-', ' ') }}</p><h2 class="mt-1 break-words text-xl font-semibold text-white">{{ details.title || details.dirname || details.format }}</h2><p v-if="details.dirname" class="mt-1 break-all font-mono text-xs text-slate-500">{{ details.dirname }}</p></div>
                <span class="shrink-0 rounded-lg border border-ps2-500/25 bg-ps2-500/10 px-2.5 py-1 text-xs font-semibold text-ps2-200">{{ details.format }}</span>
              </div>
              <div class="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
                <InfoBadge label="Size" :value="humanSize(details.size)" />
                <InfoBadge v-if="details.free != null" label="Free" :value="humanSize(details.free)" />
                <InfoBadge v-else-if="details.blocks" label="Blocks" :value="details.blocks" />
                <InfoBadge v-else label="Files" :value="details.files?.length ?? '—'" />
                <InfoBadge label="Signature" :value="details.signatureValid == null ? 'Not checked' : details.signatureValid ? 'Valid' : 'Invalid'" />
                <InfoBadge :label="isCard ? 'Saves' : 'Modified'" :value="isCard ? details.saves?.length : (details.modified || '—')" />
              </div>

              <div v-if="isPS2Card" class="mt-4">
                <div class="mb-2 flex items-center justify-between gap-3 text-xs"><span class="text-slate-400">Used {{ humanSize(details.used) }} · Free {{ humanSize(details.free) }}</span><span class="font-medium text-ps2-300">{{ usagePercent.toFixed(1) }}%</span></div>
                <div class="h-2.5 overflow-hidden rounded-full bg-black/45 ring-1 ring-inset ring-white/6"><div class="h-full rounded-full bg-gradient-to-r from-ps2-700 via-ps2-500 to-cyan-400 transition-all duration-500" :style="{ width: `${usagePercent}%` }"></div></div>
              </div>
            </div>

            <div v-if="isPS2Card" class="min-w-0 rounded-2xl border border-white/8 bg-surface/90 p-4 sm:p-5">
              <div class="mb-3 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div><h3 class="font-medium text-white">Current Card Actions</h3><p class="mt-1 text-xs text-slate-600">Actions apply to the temporary card copy currently open in this session.</p></div>
                <ToggleSwitch v-model="autoBackup" label="Auto backup" :disabled="!!activeAction" />
              </div>
              <div class="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                <div class="action-group">
                  <span class="action-group-label">Import saves</span>
                  <button class="spinner-button action-btn w-full" :disabled="!!activeAction" @click="batchInput?.click()"><SpinnerIcon v-if="isAction('batch-import')" />{{ isAction('batch-import') ? 'Importing…' : 'Batch Import…' }}</button>
                  <input ref="batchInput" type="file" multiple class="hidden" accept=".psu,.psv,.max,.cbs,.xps,.sps" @change="batchImport" />
                </div>
                <div class="action-group sm:col-span-1 xl:col-span-1">
                  <span class="action-group-label">Batch export format</span>
                  <div class="flex gap-2"><CustomSelect v-model="batchTarget" class="min-w-0 flex-1" :options="['PSV','PSU','MAX']" /><button class="spinner-button action-btn shrink-0" :disabled="!!activeAction" @click="batchExport"><SpinnerIcon v-if="isAction('batch-export')" />Export</button></div>
                </div>
                <div class="action-group">
                  <span class="action-group-label">Card health</span>
                  <button class="spinner-button action-btn w-full" :disabled="!!activeAction" @click="validate"><SpinnerIcon v-if="isAction('validate')" />{{ isAction('validate') ? 'Checking…' : 'Validate Card' }}</button>
                  <button v-if="latestValidationLog" class="mt-2 block w-full text-center text-[11px] text-ps2-300 hover:text-white" @click="validationLogOpen = true">View latest log</button>
                </div>
                <div class="action-group">
                  <span class="action-group-label">Card image</span>
                  <button class="spinner-button action-btn w-full" :disabled="!!activeAction" @click="downloadCard"><SpinnerIcon v-if="isAction('download-card')" />{{ isAction('download-card') ? 'Preparing…' : 'Download Card' }}</button>
                  <button v-if="details.hasBackup" class="spinner-button mt-2 w-full text-center text-[11px] text-slate-500 hover:text-ps2-300 disabled:opacity-50" :disabled="!!activeAction" @click="restoreBackup"><SpinnerIcon v-if="isAction('restore-backup')" />{{ isAction('restore-backup') ? 'Restoring…' : 'Restore latest backup' }}</button>
                </div>
              </div>
              <InlineAlert :message="errorFor('card-actions')" @close="clearError('card-actions')" />
            </div>

            <div v-if="isCard" class="min-w-0 rounded-2xl border border-white/8 bg-surface/90 p-4 sm:p-5">
              <div class="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <h3 class="font-medium text-white">Saves</h3>
                <div class="relative w-full sm:max-w-xs"><svg class="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-600" viewBox="0 0 20 20" fill="none" stroke="currentColor"><circle cx="8.5" cy="8.5" r="5.5"/><path d="m13 13 4 4"/></svg><input v-model="filterText" class="w-full rounded-xl border border-white/10 bg-black/30 py-2 pl-9 pr-3 text-sm text-slate-200 outline-none placeholder:text-slate-700 focus:border-ps2-500/50" placeholder="Filter title or directory…" /></div>
              </div>
              <div class="max-h-[460px] space-y-1 overflow-auto pr-1">
                <button v-for="save in filteredSaves" :key="save.dirname ?? save.slot" class="flex w-full items-center justify-between gap-3 rounded-xl px-3 py-3 text-left transition" :class="(selectedCardSave?.dirname ?? selectedCardSave?.slot) === (save.dirname ?? save.slot) ? 'bg-ps2-500/12 ring-1 ring-inset ring-ps2-500/25' : 'hover:bg-white/[0.035]'" @click="selectedSave = save.dirname ?? save.slot">
                  <span class="min-w-0"><span class="block truncate text-sm text-slate-100">{{ save.title }}</span><span class="block truncate font-mono text-[11px] text-slate-500">{{ save.dirname || save.filename }}</span></span><span class="shrink-0 text-xs text-slate-500">{{ humanSize(save.size) }}</span>
                </button>
                <div v-if="!filteredSaves.length" class="rounded-xl border border-dashed border-white/8 px-4 py-8 text-center text-sm text-slate-600">No saves match “{{ filterText }}”.</div>
              </div>
            </div>

            <div v-else-if="details.files" class="min-w-0 rounded-2xl border border-white/8 bg-surface/90 p-4 sm:p-5">
              <h3 class="mb-3 font-medium text-white">Contents</h3>
              <div class="max-w-full overflow-x-auto"><table class="w-full min-w-[560px] text-left text-sm"><thead class="text-[11px] uppercase tracking-[.12em] text-slate-600"><tr><th class="pb-2">File</th><th class="pb-2">Size</th><th class="pb-2">Mode</th><th class="pb-2">Modified</th></tr></thead><tbody class="divide-y divide-white/5"><tr v-for="f in details.files" :key="f.name"><td class="py-2.5 font-mono text-xs text-slate-200">{{ f.name }}</td><td>{{ humanSize(f.size) }}</td><td class="font-mono text-xs text-slate-500">{{ f.mode }}</td><td class="text-xs text-slate-500">{{ f.modified || '—' }}</td></tr></tbody></table></div>
            </div>
          </section>

          <aside class="min-w-0 space-y-5">
            <div v-if="isCard && selectedCardSave" class="min-w-0 rounded-2xl border border-white/8 bg-surface/90 p-4 sm:p-5">
              <div class="min-w-0"><div class="text-[10px] font-semibold uppercase tracking-[.16em] text-ps2-400">Selected Save</div><h3 class="mt-1 break-words font-medium text-white">{{ selectedCardSave.title }}</h3></div>
              <div class="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-2 text-xs"><div class="detail-tile"><span>Directory</span><b class="font-mono">{{ selectedSaveDetails?.dirname || selectedCardSave.dirname || '—' }}</b></div><div class="detail-tile"><span>Size</span><b>{{ humanSize(selectedSaveDetails?.size ?? selectedCardSave.size) }}</b></div><div class="detail-tile"><span>Files</span><b>{{ selectedSaveDetails?.fileCount ?? selectedSaveDetails?.files?.length ?? '—' }}</b></div><div class="detail-tile"><span>Modified</span><b>{{ selectedSaveDetails?.modified || '—' }}</b></div></div>
              <div class="mt-3 flex items-center justify-between gap-3">
                <button class="spinner-button text-xs text-red-300/80 hover:text-red-200 disabled:opacity-50" :disabled="!!activeAction" @click="requestDeleteSave"><SpinnerIcon v-if="isAction('delete-save')" />{{ isAction('delete-save') ? 'Deleting…' : 'Delete Save' }}</button>
                <button v-if="isPS2Card" class="text-xs text-ps2-300 hover:text-white" @click="metadataOpen = !metadataOpen">{{ metadataOpen ? 'Close editor' : 'Edit metadata…' }}</button>
              </div>
              <div v-if="metadataOpen && isPS2Card" class="mt-4 space-y-3 border-t border-white/7 pt-4"><label class="field-label">Directory / Save ID<input v-model="metadataDir" class="text-field font-mono" maxlength="31" /></label><label class="field-label">Browser title<textarea v-model="metadataTitle" rows="2" class="text-field resize-none"></textarea></label><p class="text-[11px] leading-5 text-slate-600">One newline creates a two-line PS2 Browser title.</p><button class="spinner-button w-full rounded-xl bg-ps2-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-ps2-500 disabled:opacity-40" :disabled="!!activeAction" @click="saveMetadata"><SpinnerIcon v-if="isAction('metadata')" />{{ isAction('metadata') ? 'Applying…' : 'Apply Metadata' }}</button><InlineAlert :message="errorFor('metadata')" @close="clearError('metadata')" /></div>
              <InlineAlert :message="errorFor('selected-save')" @close="clearError('selected-save')" />
            </div>

            <div class="grid grid-cols-2 gap-3 lg:grid-cols-1">
              <div class="min-w-0 rounded-2xl border border-white/8 bg-surface/90 p-3 sm:p-5">
                <div class="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between"><h3 class="font-medium text-white">Icon Preview</h3><div v-if="details.kind === 'ps2-save' || isPS2Card" class="flex w-fit rounded-lg bg-black/35 p-1 text-[10px] sm:text-xs"><button class="rounded-md px-1.5 py-1 sm:px-2" :class="previewMode === 'rendered' ? 'bg-ps2-600 text-white' : 'text-slate-500'" @click="previewMode = 'rendered'">Rendered</button><button class="rounded-md px-1.5 py-1 sm:px-2" :class="previewMode === 'texture' ? 'bg-ps2-600 text-white' : 'text-slate-500'" @click="previewMode = 'texture'">Texture</button></div></div>
                <div class="grid aspect-square place-items-center overflow-hidden rounded-xl border border-white/6 bg-black/40"><img v-if="!isCard || selectedCardSave" :key="previewUrl()" :src="previewUrl()" alt="PS1 or PS2 save icon preview" class="h-full w-full object-contain" /><div v-else class="px-2 text-center text-xs text-slate-600">Select a save</div></div>
              </div>

              <div v-if="targets.length" class="min-w-0 rounded-2xl border border-white/8 bg-surface/90 p-3 sm:p-5">
                <h3 class="mb-3 font-medium text-white">Export</h3>
                <div class="space-y-2"><CustomSelect v-model="outputFormat" class="min-w-0" :options="targetOptions" /><button class="spinner-button w-full rounded-xl bg-ps2-600 px-3 py-2.5 text-sm font-medium text-white hover:bg-ps2-500 disabled:opacity-50" :disabled="!!activeAction" @click="convert"><SpinnerIcon v-if="isAction('export')" />{{ isAction('export') ? 'Exporting…' : 'Export' }}</button></div>
                <button v-if="details.kind === 'ps2-save'" class="spinner-button mt-2 w-full rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2.5 text-xs text-slate-300 hover:border-ps2-500/30 disabled:opacity-50" :disabled="!!activeAction" @click="exportFolder"><SpinnerIcon v-if="isAction('export-folder')" />Raw folder (.zip)</button>
                <InlineAlert :message="errorFor('export')" @close="clearError('export')" />
              </div>
            </div>

            <div v-if="isPS2Card && selectedSaveDetails?.files" class="min-w-0 rounded-2xl border border-white/8 bg-surface/90 p-4 sm:p-5">
              <div class="mb-3 flex items-center justify-between"><h3 class="font-medium text-white">Files</h3><span class="text-xs text-slate-600">{{ selectedFiles.length ? `${selectedFiles.length} selected` : `${selectedSaveDetails.files.length} files` }}</span></div>
              <div class="max-h-64 space-y-1 overflow-auto rounded-xl border border-white/6 bg-black/20 p-1.5">
                <button v-for="f in selectedSaveDetails.files" :key="f.name" class="flex w-full items-center justify-between gap-3 rounded-lg px-2.5 py-2 text-left" :class="selectedFiles.includes(f.name) ? 'bg-ps2-500/15 text-white ring-1 ring-inset ring-ps2-500/25' : 'text-slate-300 hover:bg-white/5'" @click="toggleFile(f.name)"><span class="truncate font-mono text-xs">{{ f.name }}</span><span class="shrink-0 text-[11px] text-slate-600">{{ humanSize(f.size) }}</span></button>
              </div>
              <div class="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3"><button class="spinner-button file-btn" :disabled="!!activeAction" @click="extractFiles"><SpinnerIcon v-if="isAction('extract-files')" />Extract…</button><button class="spinner-button file-btn" :disabled="!!activeAction" @click="chooseReplaceFile"><SpinnerIcon v-if="isAction('replace-file')" />Replace…</button><button class="spinner-button file-btn" :disabled="!!activeAction" @click="addInput?.click()"><SpinnerIcon v-if="isAction('add-file')" />Add…</button><button class="spinner-button file-btn text-red-300 hover:border-red-500/30" :disabled="!!activeAction" @click="requestDeleteFiles"><SpinnerIcon v-if="isAction('delete-files')" />Delete</button><button class="spinner-button file-btn sm:col-span-2" :disabled="!!activeAction" @click="exportFolder"><SpinnerIcon v-if="isAction('export-folder')" />Export Folder…</button></div>
              <input ref="replaceInput" type="file" class="hidden" @change="replaceFile" /><input ref="addInput" type="file" class="hidden" @change="addFile" />
              <InlineAlert :message="errorFor('files')" @close="clearError('files')" />
            </div>
          </aside>
        </div>
      </template>
    </main>

    <footer class="relative mx-auto mt-8 flex max-w-7xl flex-col items-center gap-2 border-t border-white/6 px-4 py-5 text-center text-xs text-slate-600 sm:flex-row sm:justify-between sm:px-6 sm:text-left lg:px-8"><span>© 2026 by Chrisser73</span><a class="text-slate-500 hover:text-ps2-300" href="https://github.com/Chrisser73/" target="_blank" rel="noreferrer">Follow me on GitHub</a></footer>

    <div class="pointer-events-none fixed right-3 top-3 z-[200] flex w-[min(92vw,360px)] flex-col gap-2 sm:right-5 sm:top-5">
      <div v-for="toast in toasts" :key="toast.id" class="pointer-events-auto rounded-xl border px-4 py-3 text-sm shadow-2xl backdrop-blur" :class="toast.tone === 'error' ? 'border-red-500/30 bg-red-950/90 text-red-100' : 'border-ps2-500/30 bg-surface-raised/95 text-ps2-100'">{{ toast.text }}</div>
    </div>

    <div v-if="confirmState" class="fixed inset-0 z-[100] grid place-items-center bg-black/70 p-4 backdrop-blur-sm" @click.self="confirmState = null">
      <div class="w-full max-w-md rounded-2xl border border-white/10 bg-surface-raised p-5 shadow-2xl"><h3 class="text-lg font-semibold text-white">{{ confirmState.title }}</h3><p class="mt-2 text-sm leading-6 text-slate-400">{{ confirmState.text }}</p><div class="mt-5 flex justify-end gap-2"><button class="rounded-xl border border-white/10 px-4 py-2.5 text-sm text-slate-300 hover:bg-white/5" @click="confirmState = null">No</button><button class="rounded-xl bg-red-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-red-500" @click="confirmState.action">{{ confirmState.confirmLabel }}</button></div></div>
    </div>

    <div v-if="validationLogOpen" class="fixed inset-0 z-[110] grid place-items-center bg-black/70 p-4 backdrop-blur-sm" @click.self="validationLogOpen = false">
      <div class="w-full max-w-2xl rounded-2xl border border-white/10 bg-surface-raised p-5 shadow-2xl"><div class="flex items-center justify-between gap-4"><h3 class="text-lg font-semibold text-white">Latest validation log</h3><button class="text-xl leading-none text-slate-500 hover:text-white" @click="validationLogOpen = false">×</button></div><pre class="mt-4 max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-xl border border-white/7 bg-black/30 p-4 font-mono text-xs leading-5 text-slate-300">{{ latestValidationLog }}</pre></div>
    </div>
  </div>
</template>

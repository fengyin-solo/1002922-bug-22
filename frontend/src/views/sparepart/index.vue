<template>
  <section class="page" data-module="sparepart">
    <header class="page-head">
      <div>
        <h2>备件管理</h2>
        <p class="page-desc">出入库即时改写余量；低于最低保有量的备件会在列表标出，并汇总到待采购清单。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记备件</button>
        <button class="btn" type="button" :disabled="exporting" @click="exportRows">
          {{ exporting ? '正在导出…' : '导出盘点表' }}
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>备件编号</span>
        <input v-model="keyword" placeholder="按备件编号检索" />
      </label>
      <label class="filter-item">
        <span>备件状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>存放位置</span>
        <select v-model="locationFilter">
          <option value="">全部位置</option>
          <option v-for="location in locations" :key="location" :value="location">{{ location }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'low-stock': row.low_stock }">
          <td v-for="column in columns" :key="column">
            <template v-if="column === '当前余量'">
              {{ row[column] ?? '—' }}
              <span v-if="row.low_stock" class="badge-low">低于最低保有量</span>
            </template>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无备件数据，可先登记备件</td>
        </tr>
      </tbody>
    </table>

    <section class="purchase-panel">
      <h3>待采购清单（{{ purchaseRows.length }} 种）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in purchaseColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in purchaseRows" :key="String(row.id)">
            <td v-for="column in purchaseColumns" :key="column">{{ row[column] ?? '—' }}</td>
          </tr>
          <tr v-if="!purchaseRows.length">
            <td :colspan="purchaseColumns.length" class="empty-state">余量均不低于最低保有量，暂无待采购备件</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条备件记录</span>
      <span v-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>

type ExportPayload = {
  total: number
  excluded: number
  fields: string[]
  items: Row[]
}

const ENDPOINT = '/api/sparepart'
const columns = ["备件编号", "备件名称", "规格型号", "适用设备", "存放位置", "最低保有量", "当前余量", "备件状态"]
const purchaseColumns = ["备件编号", "备件名称", "规格型号", "适用设备", "存放位置", "当前余量", "最低保有量", "建议采购量"]
const actions = ["办理领用", "采购入仓", "停用备件"]
const statuses = ["充足", "不足", "待采购", "已停用"]
const EXPORT_MAX_ATTEMPTS = 3

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')
const locationFilter = ref('')
const locations = ref<string[]>([])
const purchaseRows = ref<Row[]>([])
const exporting = ref(false)
const stats = ref([
  { label: '备件种类', value: 0 },
  { label: '不足备件', value: 0 },
  { label: '待采购备件', value: 0 },
])

function buildQuery(): string {
  const params = new URLSearchParams()
  if (keyword.value.trim()) params.set('keyword', keyword.value.trim())
  if (statusFilter.value) params.set('status', statusFilter.value)
  if (locationFilter.value) params.set('location', locationFilter.value)
  return params.toString()
}

function rememberLocations(source: Row[]) {
  for (const row of source) {
    const location = String(row['存放位置'] ?? '').trim()
    if (location && !locations.value.includes(location)) {
      locations.value.push(location)
    }
  }
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  locationFilter.value = ''
  void reload()
}

function openCreate() {
  errorMessage.value = '备件登记入口尚未接入审批流'
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function fetchExportPayload(): Promise<ExportPayload> {
  let lastError: unknown = null
  const query = buildQuery()
  for (let attempt = 1; attempt <= EXPORT_MAX_ATTEMPTS; attempt += 1) {
    try {
      const response = await request(`${ENDPOINT}/export${query ? `?${query}` : ''}`)
      if (!response.ok) {
        throw new Error(`导出接口返回 ${response.status}`)
      }
      const payload = (await response.json()) as ExportPayload
      const items = Array.isArray(payload.items) ? payload.items : []
      // 字段没取齐、条数与页面对不上都视为没取完：重试，而不是拿占位凑数
      const incomplete = items.some((item) =>
        columns.some((column) => item[column] === undefined || item[column] === null || item[column] === ''),
      )
      if (incomplete) {
        throw new Error('导出数据字段不全')
      }
      if (Number(payload.total) + Number(payload.excluded ?? 0) !== total.value) {
        throw new Error('导出条数与列表不一致')
      }
      return payload
    } catch (error) {
      lastError = error
      if (attempt < EXPORT_MAX_ATTEMPTS) {
        await sleep(300 * attempt)
      }
    }
  }
  throw lastError instanceof Error ? lastError : new Error('导出失败')
}

function csvCell(value: unknown): string {
  const text = String(value ?? '')
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

function downloadCsv(payload: ExportPayload) {
  const header = columns.map(csvCell).join(',')
  const lines = payload.items.map((item) => columns.map((column) => csvCell(item[column])).join(','))
  const blob = new Blob(['﻿' + [header, ...lines].join('\r\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `备件盘点表-${new Date().toISOString().slice(0, 10)}.csv`
  link.click()
  URL.revokeObjectURL(url)
}

async function exportRows() {
  errorMessage.value = ''
  noticeMessage.value = ''
  exporting.value = true
  try {
    const payload = await fetchExportPayload()
    downloadCsv(payload)
    noticeMessage.value = `已按当前条件导出 ${payload.total} 条（已停用 ${payload.excluded} 条未计入盘点）`
  } catch (error) {
    errorMessage.value = error instanceof Error ? `盘点表导出失败：${error.message}` : '盘点表导出失败'
  } finally {
    exporting.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  let quantity = 1
  if (action !== '停用备件') {
    const input = window.prompt(`请输入本次${action === '办理领用' ? '领用' : '入仓'}数量`, '1')
    if (input === null) {
      return
    }
    quantity = Number(input)
    if (!Number.isInteger(quantity) || quantity <= 0) {
      errorMessage.value = '数量必须是大于 0 的整数'
      return
    }
  }
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action, quantity } }),
    })
    const result = await response.json()
    if (!response.ok || !result.ok) {
      throw new Error(result.message ?? '备件动作未生效，请稍后重试')
    }
    noticeMessage.value = result.message ?? ''
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '备件操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = buildQuery()
  try {
    const [listResponse, purchaseResponse] = await Promise.all([
      request(`${ENDPOINT}${query ? `?${query}` : ''}`),
      request(`${ENDPOINT}/to-purchase`),
    ])
    if (!listResponse.ok) {
      throw new Error('备件列表读取失败')
    }
    const payload = await listResponse.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    rememberLocations(rows.value)
    if (purchaseResponse.ok) {
      const purchase = await purchaseResponse.json()
      purchaseRows.value = purchase.items ?? []
      rememberLocations(purchaseRows.value)
    }
    stats.value = [
      { label: '备件种类', value: total.value },
      { label: '不足备件', value: purchaseRows.value.filter((row) => row['备件状态'] === '不足').length },
      { label: '待采购备件', value: purchaseRows.value.filter((row) => row['备件状态'] === '待采购').length },
    ]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '备件列表读取失败'
  }
}

onMounted(reload)
</script>

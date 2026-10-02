<template>
  <section class="page" data-module="sparepart">
    <header class="page-head">
      <div>
        <h2>备件管理</h2>
        <p class="page-desc">出入库即时更新余量；余量低于最低保有量自动标红并列入待采购清单；盘点表按当前筛选条件导出，不含已停用备件。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记备件</button>
        <button class="btn" type="button" :class="{ ghost: !showPurchase }" @click="togglePurchase">
          {{ showPurchase ? '收起待采购清单' : `待采购清单（${statsData['待采购备件']}）` }}
        </button>
        <button class="btn" type="button" :disabled="exporting" @click="exportRows">
          {{ exporting ? '导出重试中…' : '导出盘点表' }}
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card" :class="{ clickable: item.label === '待采购备件' }" @click="item.label === '待采购备件' && togglePurchase()">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value" :class="{ low: item.label !== '备件种类' && Number(item.value) > 0 }">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>备件编号/名称</span>
        <input v-model="filters.keyword" placeholder="按备件编号或名称检索" />
      </label>
      <label class="filter-item">
        <span>存放位置</span>
        <select v-model="filters.location">
          <option value="">全部位置</option>
          <option v-for="place in locations" :key="place" :value="place">{{ place }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>备件状态</span>
        <select v-model="filters.status">
          <option value="">全部在用状态</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>出入库操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'low-row': row.below_min, 'disabled-row': row['备件状态'] === '已停用' }">
          <td v-for="column in columns" :key="column">
            <template v-if="column === '当前余量' && row.below_min">
              <strong class="low">{{ row[column] }}</strong>
              <span class="tag-low">低于保有量</span>
            </template>
            <template v-else-if="column === '备件状态'">
              <span :class="['status-tag', statusClass(row[column])]">{{ row[column] }}</span>
            </template>
            <template v-else>{{ row[column] ?? 0 }}</template>
          </td>
          <td class="row-actions">
            <template v-if="row['备件状态'] !== '已停用'">
              <input
                class="qty-input"
                type="number"
                min="1"
                :value="quantities[String(row.id)] ?? 1"
                @input="quantities[String(row.id)] = Number(($event.target as HTMLInputElement).value)"
              />
              <button class="link" type="button" @click="runAction('办理领用', row)">办理领用</button>
              <button class="link" type="button" @click="runAction('采购入仓', row)">采购入仓</button>
              <button class="link danger" type="button" @click="runAction('停用备件', row)">停用备件</button>
            </template>
            <span v-else class="muted-text">已停用，不参与出入库与盘点</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">当前筛选条件下暂无备件数据</td>
        </tr>
      </tbody>
    </table>

    <section v-if="showPurchase" class="purchase-panel">
      <h3>待采购清单（{{ purchaseRows.length }}）</h3>
      <p class="page-desc">当前余量低于最低保有量、且未停用的备件，按建议采购量从大到小排列。</p>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in purchaseColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in purchaseRows" :key="String(row.id)" class="low-row">
            <td v-for="column in purchaseColumns" :key="column">{{ row[column] ?? 0 }}</td>
          </tr>
          <tr v-if="!purchaseRows.length">
            <td :colspan="purchaseColumns.length" class="empty-state">暂无低于最低保有量的备件</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条备件记录（盘点不含已停用备件）</span>
      <span v-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/sparepart'
const columns = ['备件编号', '备件名称', '规格型号', '适用设备', '存放位置', '最低保有量', '当前余量', '备件状态']
const purchaseColumns = ['备件编号', '备件名称', '规格型号', '适用设备', '存放位置', '当前余量', '最低保有量', '建议采购量']
const statuses = ['充足', '不足']

const rows = ref<Row[]>([])
const total = ref(0)
const locations = ref<string[]>([])
const purchaseRows = ref<Row[]>([])
const showPurchase = ref(false)
const exporting = ref(false)
const errorMessage = ref('')
const noticeMessage = ref('')
const quantities = ref<Record<string, number>>({})
const filters = ref({ keyword: '', location: '', status: '' })
const statsData = ref<Record<string, number>>({ 备件种类: 0, 不足备件: 0, 待采购备件: 0 })
const stats = computed(() => [
  { label: '备件种类', value: statsData.value['备件种类'] },
  { label: '不足备件', value: statsData.value['不足备件'] },
  { label: '待采购备件', value: statsData.value['待采购备件'] },
])

function buildQuery(extra: Record<string, string> = {}): string {
  const params: Record<string, string> = {}
  if (filters.value.keyword.trim()) params.keyword = filters.value.keyword.trim()
  if (filters.value.location) params.location = filters.value.location
  if (filters.value.status) params.status = filters.value.status
  Object.assign(params, extra)
  return new URLSearchParams(params).toString()
}

function resetFilters() {
  filters.value = { keyword: '', location: '', status: '' }
  void reload()
}

function openCreate() {
  errorMessage.value = '备件登记入口尚未接入审批流'
}

function statusClass(status: unknown): string {
  if (status === '不足') return 'status-low'
  if (status === '已停用') return 'status-disabled'
  return 'status-ok'
}

async function loadStats() {
  try {
    const response = await request(`${ENDPOINT}/stats`)
    if (response.ok) {
      statsData.value = await response.json()
    }
  } catch {
    // 统计卡片读取失败不阻断主列表
  }
}

async function loadPurchase() {
  const query = buildQuery()
  try {
    const response = await request(`${ENDPOINT}/purchase-list?${query}`)
    if (response.ok) {
      const payload = await response.json()
      purchaseRows.value = payload.items ?? []
    }
  } catch {
    // 待采购清单读取失败保留上次结果，不阻断操作
  }
}

function togglePurchase() {
  showPurchase.value = !showPurchase.value
  if (showPurchase.value) {
    void loadPurchase()
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  const body: Record<string, unknown> = { action }
  if (action !== '停用备件') {
    body.quantity = quantities.value[String(row.id)] ?? 1
  }
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message ?? '备件动作未生效，请稍后重试')
    }
    noticeMessage.value = payload.message ?? '操作成功'
    quantities.value[String(row.id)] = 1
    await reload()
    if (showPurchase.value) {
      await loadPurchase()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '备件操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = buildQuery()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error(`备件列表读取失败（接口返回 ${response.status}）`)
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    await loadStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '备件列表读取失败'
  }
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

async function fetchExportWithRetry<T>(): Promise<T> {
  let lastError: unknown
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      const response = await request(`${ENDPOINT}/export?${buildQuery()}`)
      if (!response.ok) {
        throw new Error(`接口返回 ${response.status}`)
      }
      return (await response.json()) as T
    } catch (error) {
      lastError = error
      if (attempt < 3) {
        await sleep(500 * attempt)
      }
    }
  }
  throw lastError instanceof Error ? lastError : new Error('盘点数据多次重试仍未取到')
}

function csvCell(value: unknown): string {
  const text = value === null || value === undefined ? '' : String(value)
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

function downloadCsv(columns: string[], items: Row[]) {
  const lines = [
    columns.map(csvCell).join(','),
    ...items.map((item) => columns.map((column) => item[column]).map(csvCell).join(',')),
  ]
  // 加 BOM，Excel 打开中文不乱码
  const blob = new Blob([`﻿${lines.join('\r\n')}`], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = '备件盘点表.csv'
  anchor.click()
  URL.revokeObjectURL(url)
}

type ExportPayload = {
  columns: string[]
  total: number
  items: Row[]
  incomplete: Array<{ id: number; 备件编号: string; 缺少字段: string[] }>
  disabled_excluded: number
}

async function exportRows() {
  errorMessage.value = ''
  noticeMessage.value = ''
  exporting.value = true
  try {
    const payload = await fetchExportWithRetry<ExportPayload>()
    downloadCsv(payload.columns, payload.items)
    const notes: string[] = [`已导出 ${payload.total} 条，与页面筛选结果一致（含适用设备列）`]
    if (payload.disabled_excluded > 0) {
      notes.push(`${payload.disabled_excluded} 条已停用备件未进盘点`)
    }
    if (payload.incomplete.length > 0) {
      const names = payload.incomplete.map((item) => item['备件编号']).join('、')
      notes.push(`${payload.incomplete.length} 条字段未取全已剔除（${names}），请补录后重新导出`)
    }
    noticeMessage.value = notes.join('；')
  } catch (error) {
    errorMessage.value = error instanceof Error ? `盘点表导出失败，已重试 3 次：${error.message}` : '盘点表导出失败'
  } finally {
    exporting.value = false
  }
}

onMounted(async () => {
  try {
    const response = await request(`${ENDPOINT}/locations`)
    if (response.ok) {
      locations.value = (await response.json()).items ?? []
    }
  } catch {
    // 位置选项取不到时退化为文本输入框场景，不阻断页面
  }
  await reload()
})
</script>

<style scoped>
.low-row {
  background: #fef3f2;
}
.disabled-row {
  background: #f2f4f7;
  color: #667085;
}
.low {
  color: #b42318;
}
.tag-low {
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #fee4e2;
  color: #b42318;
  font-size: 12px;
  white-space: nowrap;
}
.status-tag {
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 12px;
}
.status-ok {
  background: #ecfdf3;
  color: #027a48;
}
.status-low {
  background: #fee4e2;
  color: #b42318;
}
.status-disabled {
  background: #eaecf0;
  color: #475467;
}
.qty-input {
  width: 56px;
  margin-right: 8px;
  padding: 2px 4px;
  border: 1px solid var(--border);
  border-radius: 4px;
}
.link.danger {
  color: #b42318;
}
.muted-text {
  color: var(--muted);
  font-size: 12px;
}
.clickable {
  cursor: pointer;
}
.notice-text {
  color: #b54708;
}
.purchase-panel {
  margin-top: 16px;
}
.purchase-panel h3 {
  margin: 0 0 4px;
  font-size: 15px;
}
</style>

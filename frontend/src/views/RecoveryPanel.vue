<template>
  <section class="recovery-panel" :data-testid="`recovery-panel-${mode}`">
    <header class="recovery-head">
      <div>
        <h3>{{ mode === 'train' ? '中断轮次恢复' : '安全聚合 · 恢复回报接收' }}</h3>
        <p class="page-desc">
          {{
            mode === 'train'
              ? '选定训练轮次发起恢复申请，服务端核验原回报与聚合检查点后签发恢复回执；已确认回报不会被重复累计。'
              : '凭恢复回执接收恢复后的回报，并核对谁被接纳、关联的旧回执与各节点重试资格。'
          }}
        </p>
      </div>
      <button class="btn ghost" data-testid="recovery-reset" type="button" @click="resetDemo">
        重置演示场景
      </button>
    </header>

    <form class="recovery-scope" @submit.prevent="reload">
      <label class="create-field">
        <span>任务编号</span>
        <input v-model="taskId" data-testid="recovery-task" required />
      </label>
      <label class="create-field">
        <span>训练轮次</span>
        <input v-model.number="roundNo" data-testid="recovery-round" type="number" min="1" required />
      </label>
      <button class="btn" data-testid="recovery-refresh" type="submit">刷新</button>
    </form>

    <p v-if="notice" class="recovery-notice" :class="notice.kind" data-testid="recovery-notice">
      {{ notice.text }}
    </p>

    <!-- 训练页：轮次总览 + 恢复申请 + 回报提交 + 回执清单 -->
    <template v-if="mode === 'train'">
      <div class="stat-row" v-if="currentRound">
        <article class="stat-card">
          <span class="stat-label">轮次状态</span>
          <strong class="stat-value">{{ currentRound['轮次状态'] }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">参与节点</span>
          <strong class="stat-value">{{ currentRound['参与方数量'] }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">已确认</span>
          <strong class="stat-value">{{ currentRound['已确认数量'] }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">还缺节点</span>
          <strong class="stat-value" data-testid="recovery-missing">{{ currentRound['缺失数量'] }}</strong>
        </article>
      </div>

      <div class="recovery-grid">
        <form class="recovery-form" @submit.prevent="onApply">
          <h4>恢复申请</h4>
          <label class="create-field">
            <span>操作员</span>
            <input v-model="operator" data-testid="apply-operator" placeholder="值班操作员" />
          </label>
          <button class="btn primary" data-testid="apply-recovery" type="submit" :disabled="busy">
            申请恢复第 {{ roundNo }} 轮
          </button>
          <p class="form-hint">服务端核验已确认原回报与聚合检查点，返回恢复回执。</p>
        </form>

        <form class="recovery-form" @submit.prevent="onSubmitReport">
          <h4>节点回报提交</h4>
          <label class="create-field">
            <span>节点</span>
            <input v-model="reportNode" data-testid="report-node" list="recovery-nodes" required />
            <datalist id="recovery-nodes">
              <option v-for="node in currentRound?.['参与节点'] ?? []" :key="node" :value="node" />
            </datalist>
          </label>
          <label class="create-field">
            <span>回报摘要</span>
            <input v-model="reportDigest" data-testid="report-digest" placeholder="sha256:..." />
          </label>
          <label class="create-field">
            <span>恢复回执编号（恢复后回报必带）</span>
            <input v-model="reportReceipt" data-testid="report-receipt" placeholder="RCV-RCV-xxxx" />
          </label>
          <button class="btn primary" data-testid="submit-report" type="submit" :disabled="busy">
            提交回报
          </button>
        </form>
      </div>

      <h4>轮次回报接纳情况</h4>
      <table class="data-table" data-testid="recovery-round-table">
        <thead>
          <tr>
            <th>节点</th>
            <th>回报编号</th>
            <th>确认状态</th>
            <th>接纳情况</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="node in currentRound?.['参与节点'] ?? []" :key="node">
            <td>{{ node }}</td>
            <td>{{ acceptedMap[node]?.['回报编号'] ?? '—' }}</td>
            <td>{{ acceptedMap[node]?.['确认状态'] ?? '未确认' }}</td>
            <td>
              <span :class="['badge', acceptedMap[node] ? 'badge-ok' : 'badge-missing']">
                {{ acceptedMap[node] ? '已接纳' : '缺失' }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>

      <h4>恢复回执</h4>
      <table class="data-table" data-testid="recovery-receipt-table">
        <thead>
          <tr>
            <th>回执编号</th>
            <th>轮次</th>
            <th>核验已确认节点</th>
            <th>待补齐节点</th>
            <th>回执状态</th>
            <th>申请人</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="receipt in receipts" :key="receipt['回执编号']">
            <td>{{ receipt['回执编号'] }}</td>
            <td>{{ receipt['轮次'] }}</td>
            <td>{{ receipt['核验已确认节点'].join('、') }}</td>
            <td>{{ receipt['待补齐节点'].join('、') || '无' }}</td>
            <td>
              <span class="badge" :class="receipt['回执状态'] === '生效中' ? 'badge-ok' : 'badge-closed'">
                {{ receipt['回执状态'] }}
              </span>
            </td>
            <td>{{ receipt['申请人'] }}</td>
          </tr>
          <tr v-if="!receipts.length">
            <td colspan="6" class="empty-state">暂无恢复回执</td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- 聚合页：安全聚合接收 + 接纳明细/旧回执/重试资格 -->
    <template v-else>
      <form class="recovery-form agg-intake" @submit.prevent="onIntake">
        <h4>安全聚合接收恢复后回报</h4>
        <div class="recovery-grid">
          <label class="create-field">
            <span>节点</span>
            <input v-model="reportNode" data-testid="intake-node" list="agg-nodes" required />
            <datalist id="agg-nodes">
              <option v-for="node in tracking?.['轮次']?.['参与节点'] ?? []" :key="node" :value="node" />
            </datalist>
          </label>
          <label class="create-field">
            <span>回报摘要</span>
            <input v-model="reportDigest" data-testid="intake-digest" placeholder="sha256:..." />
          </label>
          <label class="create-field">
            <span>恢复回执编号</span>
            <input v-model="reportReceipt" data-testid="intake-receipt" placeholder="RCV-RCV-xxxx" />
          </label>
        </div>
        <button class="btn primary" data-testid="secure-agg-intake" type="submit" :disabled="busy">
          提交安全聚合接收
        </button>
        <p class="form-hint">无有效回执的恢复后回报将被拒绝，并保留补正后重试资格。</p>
      </form>

      <template v-if="tracking">
        <h4>轮次核对（{{ tracking['轮次']['任务编号'] }} 第 {{ tracking['轮次']['轮次'] }} 轮）</h4>
        <div class="stat-row">
          <article class="stat-card">
            <span class="stat-label">轮次状态</span>
            <strong class="stat-value">{{ tracking['轮次']['轮次状态'] }}</strong>
          </article>
          <article class="stat-card">
            <span class="stat-label">已确认</span>
            <strong class="stat-value">{{ tracking['轮次']['已确认数量'] }}/{{ tracking['轮次']['参与方数量'] }}</strong>
          </article>
          <article class="stat-card">
            <span class="stat-label">还缺节点</span>
            <strong class="stat-value" data-testid="agg-missing">{{ tracking['轮次']['缺失数量'] }}</strong>
          </article>
          <article class="stat-card">
            <span class="stat-label">聚合结果</span>
            <strong class="stat-value agg-result">{{ tracking['轮次']['聚合结果'] }}</strong>
          </article>
        </div>

        <h4>节点回报明细与重试资格</h4>
        <table class="data-table" data-testid="agg-node-table">
          <thead>
            <tr>
              <th>节点</th>
              <th>回报编号</th>
              <th>处理结论</th>
              <th>确认状态</th>
              <th>恢复后回报</th>
              <th>关联回执</th>
              <th>重试资格</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in tracking['节点回报明细']" :key="item['节点']">
              <td>{{ item['节点'] }}</td>
              <td>{{ item['回报编号'] ?? '—' }}</td>
              <td>
                <span class="badge" :class="conclusionBadge(item['处理结论'])">{{ item['处理结论'] }}</span>
              </td>
              <td>{{ item['确认状态'] }}</td>
              <td>{{ item['恢复后回报'] ? '是' : '否' }}</td>
              <td>{{ item['关联回执'] ?? '—' }}</td>
              <td>
                <span class="badge" :class="item['重试资格'] ? 'badge-retry' : 'badge-closed'">
                  {{ item['重试资格'] ? '可重试' : '不可重试' }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>

        <h4>关联的恢复回执（旧回执留痕）</h4>
        <table class="data-table" data-testid="agg-receipt-table">
          <thead>
            <tr>
              <th>回执编号</th>
              <th>检查点编号</th>
              <th>核验已确认</th>
              <th>待补齐</th>
              <th>回执状态</th>
              <th>申请时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="receipt in tracking['关联回执']" :key="receipt['回执编号']">
              <td>{{ receipt['回执编号'] }}</td>
              <td>{{ receipt['检查点编号'] }}</td>
              <td>{{ receipt['核验已确认数量'] }} 个：{{ receipt['核验已确认节点'].join('、') }}</td>
              <td>{{ receipt['待补齐数量'] }} 个：{{ receipt['待补齐节点'].join('、') || '无' }}</td>
              <td>
                <span class="badge" :class="receipt['回执状态'] === '生效中' ? 'badge-ok' : 'badge-closed'">
                  {{ receipt['回执状态'] }}
                </span>
              </td>
              <td>{{ receipt['申请时间'] }}</td>
            </tr>
            <tr v-if="!tracking['关联回执'].length">
              <td colspan="6" class="empty-state">该轮次暂无恢复回执</td>
            </tr>
          </tbody>
        </table>
      </template>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  applyRecovery,
  fetchReceipts,
  fetchRecoveryTracking,
  fetchRounds,
  resetRecoveryDemo,
  secureAggIntake,
  submitReport,
} from '@/api/recovery-service'
import type {
  RecoveryReceipt,
  RecoveryTracking,
  RoundView,
} from '@/api/recovery-types'

const props = defineProps<{ mode: 'train' | 'agg' }>()

const taskId = ref('FEDE-RCV-01')
const roundNo = ref(7)
const operator = ref('值班操作员')
const reportNode = ref('深圳核')
const reportDigest = ref('')
const reportReceipt = ref('')
const busy = ref(false)
const notice = ref<{ kind: 'ok' | 'err'; text: string } | null>(null)

const rounds = ref<RoundView[]>([])
const receipts = ref<RecoveryReceipt[]>([])
const tracking = ref<RecoveryTracking | null>(null)

const currentRound = computed<RoundView | undefined>(() =>
  rounds.value.find((item) => item['任务编号'] === taskId.value && item['轮次'] === roundNo.value),
)

const acceptedMap = computed<Record<string, RoundView['已接纳回报'][number]>>(() => {
  const map: Record<string, RoundView['已接纳回报'][number]> = {}
  for (const item of currentRound.value?.['已接纳回报'] ?? []) {
    map[item['节点']] = item
  }
  return map
})

function flash(ok: boolean, text: string) {
  notice.value = { kind: ok ? 'ok' : 'err', text }
}

function conclusionBadge(conclusion: string): string {
  if (conclusion === '已接纳') return 'badge-ok'
  if (conclusion === '待回报') return 'badge-missing'
  return 'badge-closed'
}

async function reloadTrain() {
  const [roundList, receiptList] = await Promise.all([
    fetchRounds(taskId.value),
    fetchReceipts(taskId.value),
  ])
  rounds.value = roundList
  receipts.value = receiptList
}

async function reloadAgg() {
  tracking.value = await fetchRecoveryTracking(taskId.value, roundNo.value)
}

async function reload() {
  notice.value = null
  try {
    if (props.mode === 'train') {
      await reloadTrain()
    } else {
      await reloadAgg()
    }
  } catch (error) {
    flash(false, error instanceof Error ? error.message : '数据刷新失败')
  }
}

async function onApply() {
  busy.value = true
  try {
    const result = await applyRecovery(taskId.value, roundNo.value, operator.value)
    flash(result.ok, result.message)
    if (result.ok) reportReceipt.value = result.receipt?.['回执编号'] ?? reportReceipt.value
    await reloadTrain()
  } catch (error) {
    flash(false, error instanceof Error ? error.message : '恢复申请失败')
  } finally {
    busy.value = false
  }
}

async function onSubmitReport() {
  busy.value = true
  try {
    const result = await submitReport({
      taskId: taskId.value,
      roundNo: roundNo.value,
      node: reportNode.value,
      digest: reportDigest.value,
      receiptId: reportReceipt.value || undefined,
    })
    flash(result.ok, result.message)
    await reloadTrain()
  } catch (error) {
    flash(false, error instanceof Error ? error.message : '回报提交失败')
  } finally {
    busy.value = false
  }
}

async function onIntake() {
  busy.value = true
  try {
    const result = await secureAggIntake({
      taskId: taskId.value,
      roundNo: roundNo.value,
      node: reportNode.value,
      digest: reportDigest.value,
      receiptId: reportReceipt.value || undefined,
    })
    flash(result.ok, result.message)
    await reloadAgg()
  } catch (error) {
    flash(false, error instanceof Error ? error.message : '安全聚合接收失败')
  } finally {
    busy.value = false
  }
}

async function resetDemo() {
  busy.value = true
  try {
    await resetRecoveryDemo()
    reportReceipt.value = ''
    flash(true, '演示场景已重置：3 个节点中 2 个已确认，等待恢复操作')
    await reload()
  } catch (error) {
    flash(false, error instanceof Error ? error.message : '场景重置失败')
  } finally {
    busy.value = false
  }
}

onMounted(reload)
</script>

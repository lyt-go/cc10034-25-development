/** 联邦训练中断恢复接口封装：恢复申请、回报提交、安全聚合接收与双页核对。 */
import { request } from './client'
import type {
  ApplyResult,
  RecoveryReceipt,
  RecoveryTracking,
  ReportResult,
  RoundView,
} from './recovery-types'

const BASE = '/api/federated-recovery'

async function postJson<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const response = await request(`${BASE}${path}`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  if (!response.ok) throw new Error('恢复接口请求失败')
  return (await response.json()) as T
}

async function getJson<T>(path: string): Promise<T> {
  const response = await request(`${BASE}${path}`)
  if (!response.ok) throw new Error('恢复数据读取失败')
  return (await response.json()) as T
}

/** 轮次总览：谁被接纳、当前还缺几个节点。 */
export async function fetchRounds(taskId?: string): Promise<RoundView[]> {
  const query = taskId ? `?task_id=${encodeURIComponent(taskId)}` : ''
  const payload = await getJson<{ items: RoundView[] }>(`/rounds${query}`)
  return payload.items
}

export async function fetchRound(taskId: string, roundNo: number): Promise<RoundView> {
  return getJson<RoundView>(`/rounds/${encodeURIComponent(taskId)}/${roundNo}`)
}

/** 恢复回执清单，供训练页与聚合页关联核对。 */
export async function fetchReceipts(taskId?: string): Promise<RecoveryReceipt[]> {
  const query = taskId ? `?task_id=${encodeURIComponent(taskId)}` : ''
  const payload = await getJson<{ items: RecoveryReceipt[] }>(`/receipts${query}`)
  return payload.items
}

/** 恢复申请：选定轮次，服务端核验原回报与聚合检查点并返回回执。 */
export async function applyRecovery(
  taskId: string,
  roundNo: number,
  operator: string,
): Promise<ApplyResult> {
  return postJson<ApplyResult>('/apply', { task_id: taskId, round_no: roundNo, operator })
}

/** 节点回报提交：幂等防重、终态拦截、恢复授权由服务端裁定。 */
export async function submitReport(payload: {
  taskId: string
  roundNo: number
  node: string
  digest?: string
  receiptId?: string
}): Promise<ReportResult> {
  return postJson<ReportResult>('/reports', {
    task_id: payload.taskId,
    round_no: payload.roundNo,
    node: payload.node,
    digest: payload.digest ?? '',
    receipt_id: payload.receiptId ?? null,
  })
}

/** 安全聚合凭恢复回执接收恢复后的回报。 */
export async function secureAggIntake(payload: {
  taskId: string
  roundNo: number
  node: string
  digest?: string
  receiptId?: string
}): Promise<ReportResult & { 接收状态?: string; 关联回执?: string | null; 回报编号?: string }> {
  return postJson('/secure-agg/intake', {
    task_id: payload.taskId,
    round_no: payload.roundNo,
    node: payload.node,
    digest: payload.digest ?? '',
    receipt_id: payload.receiptId ?? null,
  })
}

/** 聚合页核对：接纳明细、关联的旧回执与每个节点的重试资格。 */
export async function fetchRecoveryTracking(
  taskId: string,
  roundNo: number,
): Promise<RecoveryTracking> {
  return getJson<RecoveryTracking>(
    `/secure-agg/tracking/${encodeURIComponent(taskId)}/${roundNo}`,
  )
}

/** 重置为题设三节点演示场景。 */
export async function resetRecoveryDemo(): Promise<RoundView> {
  return postJson<RoundView>('/demo/reset', {})
}

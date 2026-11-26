/** 联邦训练中断恢复的接口契约，与后端 federated_recovery 服务返回结构对齐。 */

export type RoundView = {
  ok?: boolean
  任务编号: string
  轮次: number
  轮次状态: string
  参与节点: string[]
  参与方数量: number
  已确认节点: string[]
  已确认数量: number
  已接纳回报: { 节点: string; 回报编号: string; 确认状态: string }[]
  缺失节点: string[]
  缺失数量: number
  检查点编号: string
  检查点状态: string
  聚合结果: string
  最新恢复回执: string | null
  message?: string
}

export type RecoveryReceipt = {
  回执编号: string
  任务编号: string
  轮次: number
  申请人: string
  申请时间: string
  检查点编号: string
  核验已确认节点: string[]
  核验已确认数量: number
  待补齐节点: string[]
  待补齐数量: number
  回执状态: string
  重复累计防护: string
}

export type NodeReportStatus = {
  节点: string
  回报编号: string | null
  处理结论: string
  确认状态: string
  恢复后回报: boolean
  关联回执: string | null
  重试资格: boolean
}

export type RecoveryTracking = {
  ok: boolean
  轮次: RoundView
  关联回执: RecoveryReceipt[]
  节点回报明细: NodeReportStatus[]
  message?: string
}

export type ReportResult = {
  ok: boolean
  message: string
  receipt_status?: string
  retry_eligible?: boolean
  report?: {
    回报编号: string
    节点: string
    确认状态: string
    处理结论: string
    恢复后回报: boolean
    关联回执: string | null
  }
  round?: RoundView
}

export type ApplyResult = {
  ok: boolean
  message: string
  receipt?: RecoveryReceipt
  round?: RoundView
  checkpoint?: string
}

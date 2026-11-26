"""安全聚合领域服务：通用资源骨架 + 恢复后回报的安全接收。

恢复后的联邦回报进入安全聚合前必须出示有效恢复回执；无回执或回执已核销时拒绝，
并给出 retry_eligible 标记，便于操作员判断是否可以补正后重试。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.federated_recovery import (
    FederatedRecoveryService,
    federated_recovery_service,
)


class SecureAggService(BaseModuleService):
    MODULE = "secure_agg"
    ENTITY = "聚合任务"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["任务编号", "数据源列表", "聚合函数", "参与方数量", "最小阈值", "聚合结果", "执行时间", "验证状态"]
    REQUIRED_FIELDS = ["任务编号", "数据源列表", "聚合函数"]
    STATUS_ORDER = ["待执行", "执行中", "已完成", "验证失败"]
    ACTION_RULES = {"发起聚合": "执行中", "标记完成": "已完成", "标记失败": "验证失败"}
    NEGATIVE_ACTIONS = []

    def __init__(self, recovery_service: FederatedRecoveryService | None = None) -> None:
        # 默认挂全局单例；测试或多任务场景可注入独立的恢复服务实例。
        self.recovery = recovery_service or federated_recovery_service

    # ------------------------------------------------------------------
    # 安全聚合接收恢复后的回报
    # ------------------------------------------------------------------
    def intake_recovered_report(
        self,
        task_id: str,
        round_no: int,
        node: str,
        digest: str,
        receipt_id: str | None,
    ) -> dict[str, Any]:
        """安全聚合入口：核验恢复回执后再接收回报。

        回执无效 -> 拒收且 retry_eligible=True（补正回执后可重试）；
        回执有效 -> 委托联邦恢复服务做幂等接纳，聚合侧只认已确认回报。
        """
        if not receipt_id or not self.recovery._receipt_usable(
            receipt_id, task_id, round_no
        ):
            return {
                "ok": False,
                "接收状态": "拒绝接收",
                "retry_eligible": True,
                "message": "安全聚合未收到有效恢复回执，回报已拒绝，可补回执后重试",
                "关联回执": receipt_id,
            }

        result = self.recovery.submit_report(
            task_id, round_no, node, digest, receipt_id=receipt_id
        )
        if not result["ok"]:
            # 重复回报/终态轮次等：聚合侧同样不累计，并透传重试资格。
            return {
                "ok": False,
                "接收状态": result.get("receipt_status", "未接收"),
                "retry_eligible": bool(result.get("retry_eligible", False)),
                "message": result["message"],
                "关联回执": receipt_id,
                "round": result.get("round"),
            }

        round_view = result["round"]
        return {
            "ok": True,
            "接收状态": "已接收",
            "retry_eligible": False,
            "message": f"安全聚合凭回执 {receipt_id} 接收 {node} 的恢复后回报",
            "关联回执": receipt_id,
            "回报编号": result["report"]["回报编号"],
            "round": round_view,
        }

    def recovery_tracking(self, task_id: str, round_no: int) -> dict[str, Any]:
        """聚合页核对：轮次接纳情况、关联的旧回执与各节点重试资格。"""
        overview = self.recovery.round_overview(task_id, round_no)
        if not overview.get("ok"):
            return overview
        receipts = self.recovery.list_receipts(task_id)
        round_receipts = [r for r in receipts if r["轮次"] == round_no]
        reports = self.recovery._round_reports(task_id, round_no)
        node_status = []
        for report in reports:
            conclusion = report["处理结论"]
            node_status.append({
                "节点": report["节点"],
                "回报编号": report["回报编号"],
                "处理结论": conclusion,
                "确认状态": report["确认状态"],
                "恢复后回报": report["恢复后回报"],
                "关联回执": report["关联回执"],
                # 已接纳/重复/迟到均不可重试；仅授权失败（未授权恢复）可补回执重试。
                "重试资格": conclusion == "未授权恢复",
            })
        accepted_nodes = {item["节点"] for item in node_status if item["处理结论"] == "已接纳"}
        for node in overview["参与节点"]:
            if node not in accepted_nodes:
                node_status.append({
                    "节点": node,
                    "回报编号": None,
                    "处理结论": "待回报",
                    "确认状态": "未确认",
                    "恢复后回报": False,
                    "关联回执": None,
                    "重试资格": True,
                })
        return {
            "ok": True,
            "轮次": overview,
            "关联回执": [dict(r) for r in round_receipts],
            "节点回报明细": node_status,
        }

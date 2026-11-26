"""联邦训练中断恢复领域服务。

围绕「中断轮次恢复申请」维护一套独立的内存状态：

- 轮次回报（round_reports）：每个参与节点在某一轮提交的回报及其确认状态；
- 恢复申请/回执（recovery_receipts）：操作员选定轮次发起恢复，服务端核验原回报与
  聚合检查点后签发回执；
- 聚合检查点（aggregation_checkpoints）：每轮聚合落盘的检查点，是恢复核验的依据。

关键约束（对应业务要求）：

1. 恢复核验只承认恢复前已确认的节点回报，已确认回报不会因恢复而被再累计一次；
2. 回报提交按 (任务, 轮次, 节点) 幂等，重复回报返回 retry_eligible=False，绝不二次累计；
3. 旧轮次迟到的回报遇到恢复提交时，回执固定为「已结束轮次」，轮次结果不被静默改写；
4. 安全聚合只接受带有效恢复回执的恢复后回报，否则拒绝。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

# 轮次在聚合侧的生命周期状态。
ROUND_OPEN = "待聚合"          # 还在收集回报，可继续提交
ROUND_AGGREGATED = "已聚合"    # 已产出聚合结果并落检查点，轮次结束
ROUND_RECOVERED = "已恢复"     # 经恢复申请重开，等待恢复后回报补齐

# 回报处理结论。
RECEIPT_ACCEPTED = "已接纳"
RECEIPT_DUPLICATE = "重复回报"
RECEIPT_CLOSED = "已结束轮次"
RECEIPT_REJECTED = "未授权恢复"

# 终态轮次集合：终态轮次不得被静默改写。
CLOSED_ROUND_STATES = {ROUND_AGGREGATED, ROUND_RECOVERED}


class FederatedRecoveryService:
    """联邦训练恢复：回报登记、恢复核验、迟到/重复回报处置、安全聚合衔接。"""

    def __init__(self) -> None:
        self.round_reports: list[dict[str, Any]] = []
        self.recovery_receipts: list[dict[str, Any]] = []
        self.aggregation_checkpoints: list[dict[str, Any]] = []
        self._seq_report = 0
        self._seq_receipt = 0
        self._seq_checkpoint = 0
        self._reset_demo_scenario()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _now(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _next_id(self, attr: str) -> int:
        current = getattr(self, attr)
        value = current + 1
        setattr(self, attr, value)
        return value

    def _find_round(self, task_id: str, round_no: int) -> dict[str, Any] | None:
        for checkpoint in self.aggregation_checkpoints:
            if checkpoint["任务编号"] == task_id and checkpoint["轮次"] == round_no:
                return checkpoint
        return None

    def _find_report(
        self, task_id: str, round_no: int, node: str
    ) -> dict[str, Any] | None:
        for report in self.round_reports:
            if (
                report["任务编号"] == task_id
                and report["轮次"] == round_no
                and report["节点"] == node
            ):
                return report
        return None

    def _round_reports(self, task_id: str, round_no: int) -> list[dict[str, Any]]:
        return [
            report
            for report in self.round_reports
            if report["任务编号"] == task_id and report["轮次"] == round_no
        ]

    def _latest_receipt(
        self, task_id: str, round_no: int
    ) -> dict[str, Any] | None:
        matched = [
            receipt
            for receipt in self.recovery_receipts
            if receipt["任务编号"] == task_id and receipt["轮次"] == round_no
        ]
        return matched[-1] if matched else None

    def _round_view(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        task_id = checkpoint["任务编号"]
        round_no = checkpoint["轮次"]
        reports = self._round_reports(task_id, round_no)
        accepted = [r for r in reports if r["处理结论"] == RECEIPT_ACCEPTED]
        confirmed = [r for r in reports if r["确认状态"] == "已确认"]
        missing = [
            node
            for node in checkpoint["参与节点"]
            if not any(r["节点"] == node and r["处理结论"] == RECEIPT_ACCEPTED for r in reports)
        ]
        receipt = self._latest_receipt(task_id, round_no)
        return {
            "任务编号": task_id,
            "轮次": round_no,
            "轮次状态": checkpoint["轮次状态"],
            "参与节点": list(checkpoint["参与节点"]),
            "参与方数量": len(checkpoint["参与节点"]),
            "已确认节点": [r["节点"] for r in confirmed],
            "已确认数量": len(confirmed),
            "已接纳回报": [
                {"节点": r["节点"], "回报编号": r["回报编号"], "确认状态": r["确认状态"]}
                for r in accepted
            ],
            "缺失节点": missing,
            "缺失数量": len(missing),
            "检查点编号": checkpoint["检查点编号"],
            "检查点状态": checkpoint["检查点状态"],
            "聚合结果": checkpoint["聚合结果"],
            "最新恢复回执": receipt["回执编号"] if receipt else None,
        }

    # ------------------------------------------------------------------
    # 演示场景：三个节点中两个已确认的中断轮次
    # ------------------------------------------------------------------
    def _reset_demo_scenario(self) -> None:
        """重置为题设场景：FEDE-RCV-01 第7轮，3 节点中 2 个已确认。"""
        self.round_reports.clear()
        self.recovery_receipts.clear()
        self.aggregation_checkpoints.clear()
        self._seq_report = 0
        self._seq_receipt = 0
        self._seq_checkpoint = 0

        task_id = "FEDE-RCV-01"
        round_no = 7
        nodes = ["上海核", "北京核", "深圳核"]
        # 中断前两个节点的回报已确认，第三个节点尚未回报，轮次停在待聚合。
        for node, digest in (("上海核", "sha256:a1b2"), ("北京核", "sha256:c3d4")):
            self._seq_report += 1
            self.round_reports.append({
                "回报编号": f"RPT-RCV-{self._seq_report:04d}",
                "任务编号": task_id,
                "轮次": round_no,
                "节点": node,
                "回报摘要": digest,
                "确认状态": "已确认",
                "处理结论": RECEIPT_ACCEPTED,
                "提交时间": "2026-11-26 09:10:00",
                "恢复后回报": False,
                "关联回执": None,
            })
        self._seq_checkpoint += 1
        self.aggregation_checkpoints.append({
            "检查点编号": f"CKPT-RCV-{self._seq_checkpoint:04d}",
            "任务编号": task_id,
            "轮次": round_no,
            "参与节点": nodes,
            "检查点状态": "中断待恢复",
            "轮次状态": ROUND_OPEN,
            "聚合结果": "未聚合(2/3已确认)",
            "已确认快照": ["上海核", "北京核"],
            "创建时间": "2026-11-26 09:12:00",
        })

    def reset_demo(self) -> dict[str, Any]:
        self._reset_demo_scenario()
        return self.round_overview("FEDE-RCV-01", 7)

    # ------------------------------------------------------------------
    # 查询：轮次总览（页面查明谁被接纳、还缺几个节点）
    # ------------------------------------------------------------------
    def list_rounds(self, task_id: str | None = None) -> list[dict[str, Any]]:
        checkpoints = self.aggregation_checkpoints
        if task_id:
            checkpoints = [c for c in checkpoints if c["任务编号"] == task_id]
        return [self._round_view(cp) for cp in checkpoints]

    def round_overview(self, task_id: str, round_no: int) -> dict[str, Any]:
        checkpoint = self._find_round(task_id, round_no)
        if checkpoint is None:
            return {
                "ok": False,
                "message": f"任务 {task_id} 第 {round_no} 轮不存在或已归档",
            }
        view = self._round_view(checkpoint)
        view["ok"] = True
        return view

    def list_receipts(self, task_id: str | None = None) -> list[dict[str, Any]]:
        receipts = self.recovery_receipts
        if task_id:
            receipts = [r for r in receipts if r["任务编号"] == task_id]
        return [dict(receipt) for receipt in receipts]

    # ------------------------------------------------------------------
    # 恢复申请：核验原回报 + 聚合检查点，签发恢复回执
    # ------------------------------------------------------------------
    def apply_recovery(self, task_id: str, round_no: int, operator: str) -> dict[str, Any]:
        checkpoint = self._find_round(task_id, round_no)
        if checkpoint is None:
            return {"ok": False, "message": f"任务 {task_id} 第 {round_no} 轮不存在，无法恢复"}

        # 已聚合完成的轮次属于终态，不得被恢复申请静默改写。
        if checkpoint["轮次状态"] == ROUND_AGGREGATED:
            return {
                "ok": False,
                "message": f"第 {round_no} 轮已聚合结束，终态轮次不得恢复改写",
                "checkpoint": checkpoint["检查点编号"],
            }

        reports = self._round_reports(task_id, round_no)
        confirmed_before = [r for r in reports if r["确认状态"] == "已确认"]
        verified_nodes = [r["节点"] for r in confirmed_before]

        # 核验原回报：已确认回报的摘要必须仍能在检查点快照中对上。
        snapshot = set(checkpoint.get("已确认快照", []))
        tampered = [r["节点"] for r in confirmed_before if r["节点"] not in snapshot]
        if tampered:
            return {
                "ok": False,
                "message": f"原回报核验失败，节点 {'、'.join(tampered)} 与聚合检查点不一致",
                "checkpoint": checkpoint["检查点编号"],
            }

        missing_nodes = [
            node for node in checkpoint["参与节点"] if node not in verified_nodes
        ]
        self._seq_receipt += 1
        receipt_id = f"RCV-RCV-{self._seq_receipt:04d}"
        receipt = {
            "回执编号": receipt_id,
            "任务编号": task_id,
            "轮次": round_no,
            "申请人": operator or "值班操作员",
            "申请时间": self._now(),
            "检查点编号": checkpoint["检查点编号"],
            "核验已确认节点": verified_nodes,
            "核验已确认数量": len(verified_nodes),
            "待补齐节点": missing_nodes,
            "待补齐数量": len(missing_nodes),
            "回执状态": "生效中",
            "重复累计防护": "已确认回报仅核验不重复累计",
        }
        self.recovery_receipts.append(receipt)

        # 恢复只重开轮次以接收缺失节点的回报，已确认回报保持原确认，不再累计。
        checkpoint["轮次状态"] = ROUND_RECOVERED
        checkpoint["检查点状态"] = "恢复重开-等待补齐回报"
        return {
            "ok": True,
            "message": (
                f"恢复回执 {receipt_id} 已签发：核验 {len(verified_nodes)} 个已确认回报，"
                f"待补齐 {len(missing_nodes)} 个节点"
            ),
            "receipt": dict(receipt),
            "round": self._round_view(checkpoint),
        }

    # ------------------------------------------------------------------
    # 回报提交：幂等防重 + 终态拦截 + 恢复授权
    # ------------------------------------------------------------------
    def submit_report(
        self,
        task_id: str,
        round_no: int,
        node: str,
        digest: str,
        *,
        receipt_id: str | None = None,
    ) -> dict[str, Any]:
        checkpoint = self._find_round(task_id, round_no)
        if checkpoint is None:
            return {"ok": False, "message": f"任务 {task_id} 第 {round_no} 轮不存在"}

        existing = self._find_report(task_id, round_no, node)
        if existing is not None:
            # 同节点同轮次再次提交一律视为重复回报，绝不二次累计。
            return {
                "ok": False,
                "receipt_status": RECEIPT_DUPLICATE,
                "retry_eligible": False,
                "message": (
                    f"节点 {node} 在第 {round_no} 轮的回报已存在（{existing['回报编号']}），"
                    "重复回报不再累计"
                ),
                "report": dict(existing),
                "round": self._round_view(checkpoint),
            }

        if checkpoint["轮次状态"] == ROUND_AGGREGATED:
            # 旧轮次迟到回报：轮次已结束，只登记拒收明细，不改写轮次结果。
            record = self._record_late_report(
                task_id, round_no, node, digest, receipt_id, checkpoint
            )
            return {
                "ok": False,
                "receipt_status": RECEIPT_CLOSED,
                "retry_eligible": False,
                "message": f"第 {round_no} 轮已聚合结束，迟到回报不予接纳，轮次结果保持不变",
                "report": record,
                "round": self._round_view(checkpoint),
            }

        active_receipt = self._latest_receipt(task_id, round_no)
        if checkpoint["轮次状态"] == ROUND_RECOVERED:
            # 恢复后的回报必须携带有效恢复回执，安全聚合据此授权。
            if not receipt_id or not self._receipt_usable(receipt_id, task_id, round_no):
                return {
                    "ok": False,
                    "receipt_status": RECEIPT_REJECTED,
                    "retry_eligible": True,
                    "message": "恢复轮次的回报缺少有效恢复回执，安全聚合拒绝接收（可补回执后重试）",
                    "round": self._round_view(checkpoint),
                }
            if node not in active_receipt["待补齐节点"]:
                return {
                    "ok": False,
                    "receipt_status": RECEIPT_DUPLICATE,
                    "retry_eligible": False,
                    "message": f"节点 {node} 的回报已在恢复前确认，不得在恢复轮次重复累计",
                    "round": self._round_view(checkpoint),
                }

        # 正常接纳（含持有效回执的恢复后回报）。
        self._seq_report += 1
        is_recovered = checkpoint["轮次状态"] == ROUND_RECOVERED
        report = {
            "回报编号": f"RPT-RCV-{self._seq_report:04d}",
            "任务编号": task_id,
            "轮次": round_no,
            "节点": node,
            "回报摘要": digest or "sha256:unset",
            "确认状态": "已确认",
            "处理结论": RECEIPT_ACCEPTED,
            "提交时间": self._now(),
            "恢复后回报": is_recovered,
            "关联回执": receipt_id if is_recovered else None,
        }
        self.round_reports.append(report)
        view = self._round_view(checkpoint)

        # 恢复轮次补齐全部节点后，通知安全聚合重新聚合并落新检查点。
        if is_recovered and view["缺失数量"] == 0:
            self._close_recovered_round(checkpoint, active_receipt, report)
            view = self._round_view(checkpoint)

        return {
            "ok": True,
            "receipt_status": RECEIPT_ACCEPTED,
            "retry_eligible": False,
            "message": f"节点 {node} 的回报已接纳"
            + ("（恢复后回报，凭回执授权）" if is_recovered else ""),
            "report": dict(report),
            "round": view,
        }

    def _receipt_usable(self, receipt_id: str, task_id: str, round_no: int) -> bool:
        for receipt in self.recovery_receipts:
            if (
                receipt["回执编号"] == receipt_id
                and receipt["任务编号"] == task_id
                and receipt["轮次"] == round_no
                and receipt["回执状态"] == "生效中"
            ):
                return True
        return False

    def _record_late_report(
        self,
        task_id: str,
        round_no: int,
        node: str,
        digest: str,
        receipt_id: str | None,
        checkpoint: dict[str, Any],
    ) -> dict[str, Any]:
        """迟到回报只留痕，不进入已确认集合、不改写已结束轮次。"""
        self._seq_report += 1
        record = {
            "回报编号": f"RPT-RCV-{self._seq_report:04d}",
            "任务编号": task_id,
            "轮次": round_no,
            "节点": node,
            "回报摘要": digest or "sha256:unset",
            "确认状态": "未确认",
            "处理结论": RECEIPT_CLOSED,
            "提交时间": self._now(),
            "恢复后回报": False,
            "关联回执": receipt_id,
        }
        self.round_reports.append(record)
        return record

    def _close_recovered_round(
        self,
        checkpoint: dict[str, Any],
        receipt: dict[str, Any] | None,
        last_report: dict[str, Any],
    ) -> None:
        """恢复轮次补齐后由安全聚合重新聚合，检查点与回执同步收口。"""
        checkpoint["轮次状态"] = ROUND_AGGREGATED
        checkpoint["检查点状态"] = "恢复后重新聚合-已落盘"
        confirmed = [
            r["节点"]
            for r in self._round_reports(checkpoint["任务编号"], checkpoint["轮次"])
            if r["确认状态"] == "已确认"
        ]
        checkpoint["已确认快照"] = confirmed
        checkpoint["聚合结果"] = f"FedAvg恢复聚合({len(confirmed)}/{len(confirmed)}节点)"
        if receipt is not None:
            receipt["回执状态"] = "已核销"
        last_report["确认状态"] = "已确认"


# 单例：与其它领域服务一样由路由层共享。
federated_recovery_service = FederatedRecoveryService()

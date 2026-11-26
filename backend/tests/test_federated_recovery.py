"""联邦训练中断恢复测试：核验、幂等防重、终态保护、安全聚合衔接。

运行：cd backend && python3 -m pytest tests/test_federated_recovery.py -v
"""
from __future__ import annotations

import pytest

from app.services.federated_recovery import (
    CLOSED_ROUND_STATES,
    RECEIPT_ACCEPTED,
    RECEIPT_CLOSED,
    RECEIPT_DUPLICATE,
    RECEIPT_REJECTED,
    ROUND_AGGREGATED,
    ROUND_RECOVERED,
    FederatedRecoveryService,
)
from app.services.secure_agg import SecureAggService

TASK = "FEDE-RCV-01"
ROUND = 7
NODE_A, NODE_B, NODE_C = "上海核", "北京核", "深圳核"


@pytest.fixture()
def service() -> FederatedRecoveryService:
    return FederatedRecoveryService()


# ================================================================
# 1. 初始场景：三节点中两个已确认
# ================================================================

class TestDemoScenario:
    def test_seed_has_two_confirmed_of_three(self, service: FederatedRecoveryService):
        overview = service.round_overview(TASK, ROUND)
        assert overview["ok"] is True
        assert overview["参与方数量"] == 3
        assert overview["已确认数量"] == 2
        assert set(overview["已确认节点"]) == {NODE_A, NODE_B}
        assert overview["缺失节点"] == [NODE_C]
        assert overview["缺失数量"] == 1
        assert overview["轮次状态"] not in CLOSED_ROUND_STATES or overview["轮次状态"] == "待聚合"

    def test_reset_demo_restores_scenario(self, service: FederatedRecoveryService):
        service.apply_recovery(TASK, ROUND, "操作员")
        reset = service.reset_demo()
        assert reset["已确认数量"] == 2
        assert reset["缺失数量"] == 1
        assert service.list_receipts(TASK) == []


# ================================================================
# 2. 恢复申请：核验原回报与检查点，签发回执
# ================================================================

class TestRecoveryApply:
    def test_apply_verifies_confirmed_reports_and_checkpoint(
        self, service: FederatedRecoveryService
    ):
        result = service.apply_recovery(TASK, ROUND, "操作员-林")
        assert result["ok"] is True
        receipt = result["receipt"]
        assert receipt["回执编号"].startswith("RCV-RCV-")
        assert receipt["检查点编号"].startswith("CKPT-RCV-")
        assert set(receipt["核验已确认节点"]) == {NODE_A, NODE_B}
        assert receipt["待补齐节点"] == [NODE_C]
        assert result["round"]["轮次状态"] == ROUND_RECOVERED

    def test_apply_does_not_reaccumulate_confirmed_reports(
        self, service: FederatedRecoveryService
    ):
        before = service.round_overview(TASK, ROUND)
        service.apply_recovery(TASK, ROUND, "操作员-林")
        after = service.round_overview(TASK, ROUND)
        # 已确认回报数量与节点保持不变，恢复只重开轮次、不重复累计。
        assert after["已确认数量"] == before["已确认数量"] == 2
        assert after["已确认节点"] == before["已确认节点"]
        reports = service._round_reports(TASK, ROUND)
        assert len([r for r in reports if r["确认状态"] == "已确认"]) == 2

    def test_apply_unknown_round_rejected(self, service: FederatedRecoveryService):
        result = service.apply_recovery(TASK, 999, "操作员")
        assert result["ok"] is False
        assert "不存在" in result["message"]

    def test_apply_on_aggregated_round_rejected(self, service: FederatedRecoveryService):
        # 先把轮次推进到已聚合终态，再尝试恢复必须被拒绝。
        checkpoint = service._find_round(TASK, ROUND)
        checkpoint["轮次状态"] = ROUND_AGGREGATED
        result = service.apply_recovery(TASK, ROUND, "操作员")
        assert result["ok"] is False
        assert "终态" in result["message"]
        assert checkpoint["轮次状态"] == ROUND_AGGREGATED


# ================================================================
# 3. 恢复后回报：回执授权 + 迟到/重复回报处置
# ================================================================

class TestReportSubmission:
    def test_recovered_report_requires_receipt(self, service: FederatedRecoveryService):
        service.apply_recovery(TASK, ROUND, "操作员")
        result = service.submit_report(TASK, ROUND, NODE_C, "sha256:e5f6")
        assert result["ok"] is False
        assert result["receipt_status"] == RECEIPT_REJECTED
        assert result["retry_eligible"] is True
        # 被拒回报不留痕、不累计。
        assert service.round_overview(TASK, ROUND)["缺失数量"] == 1

    def test_third_node_accepted_with_receipt_and_closes_round(
        self, service: FederatedRecoveryService
    ):
        receipt = service.apply_recovery(TASK, ROUND, "操作员")["receipt"]
        result = service.submit_report(
            TASK, ROUND, NODE_C, "sha256:e5f6", receipt_id=receipt["回执编号"]
        )
        assert result["ok"] is True
        assert result["receipt_status"] == RECEIPT_ACCEPTED
        assert result["report"]["恢复后回报"] is True
        assert result["report"]["关联回执"] == receipt["回执编号"]
        overview = result["round"]
        assert overview["缺失数量"] == 0
        assert overview["轮次状态"] == ROUND_AGGREGATED
        assert overview["已确认数量"] == 3

    def test_duplicate_report_not_accumulated(self, service: FederatedRecoveryService):
        receipt = service.apply_recovery(TASK, ROUND, "操作员")["receipt"]
        service.submit_report(TASK, ROUND, NODE_C, "sha256:e5f6", receipt_id=receipt["回执编号"])
        # 交错送来的重复回报（同节点同轮次再次提交）。
        dup = service.submit_report(
            TASK, ROUND, NODE_C, "sha256:different", receipt_id=receipt["回执编号"]
        )
        assert dup["ok"] is False
        assert dup["receipt_status"] == RECEIPT_DUPLICATE
        assert dup["retry_eligible"] is False
        reports = service._round_reports(TASK, ROUND)
        # 已确认回报仍只有 3 条，重复回报没有再累计一次。
        confirmed = [r for r in reports if r["确认状态"] == "已确认"]
        assert len(confirmed) == 3
        c_reports = [r for r in reports if r["节点"] == NODE_C]
        assert len(c_reports) == 1
        assert c_reports[0]["回报摘要"] == "sha256:e5f6"

    def test_confirmed_node_cannot_resubmit_after_recovery(
        self, service: FederatedRecoveryService
    ):
        receipt = service.apply_recovery(TASK, ROUND, "操作员")["receipt"]
        # 恢复前已确认的节点若重复提交，按重复回报拦截，不二次累计。
        dup = service.submit_report(
            TASK, ROUND, NODE_A, "sha256:aaaa", receipt_id=receipt["回执编号"]
        )
        assert dup["ok"] is False
        assert dup["receipt_status"] == RECEIPT_DUPLICATE
        assert dup["retry_eligible"] is False
        assert service.round_overview(TASK, ROUND)["已确认数量"] == 2

    def test_late_report_on_closed_round_is_traceable(self, service: FederatedRecoveryService):
        receipt = service.apply_recovery(TASK, ROUND, "操作员")["receipt"]
        service.submit_report(TASK, ROUND, NODE_C, "sha256:e5f6", receipt_id=receipt["回执编号"])
        # 轮次已聚合结束后，旧轮次迟到回报只留痕、不改写结果。
        late = service.submit_report(
            TASK, ROUND, "迟到核", "sha256:late", receipt_id=receipt["回执编号"]
        )
        assert late["ok"] is False
        assert late["receipt_status"] == RECEIPT_CLOSED
        assert late["retry_eligible"] is False
        record = late["report"]
        assert record["处理结论"] == RECEIPT_CLOSED
        assert record["确认状态"] == "未确认"
        overview = service.round_overview(TASK, ROUND)
        assert overview["轮次状态"] == ROUND_AGGREGATED
        assert overview["已确认数量"] == 3
        assert "迟到核" not in overview["已确认节点"]

    def test_closed_round_not_silently_rewritten(self, service: FederatedRecoveryService):
        receipt = service.apply_recovery(TASK, ROUND, "操作员")["receipt"]
        service.submit_report(TASK, ROUND, NODE_C, "sha256:e5f6", receipt_id=receipt["回执编号"])
        checkpoint_before = service._find_round(TASK, ROUND)
        result_text = checkpoint_before["聚合结果"]
        service.submit_report(TASK, ROUND, NODE_C, "sha256:again")
        service.submit_report(TASK, ROUND, "迟到核", "sha256:late")
        checkpoint_after = service._find_round(TASK, ROUND)
        assert checkpoint_after["聚合结果"] == result_text
        assert checkpoint_after["轮次状态"] == ROUND_AGGREGATED


# ================================================================
# 4. 安全聚合：回执授权接收 + 聚合页核对
# ================================================================

class TestSecureAggIntegration:
    def test_intake_rejects_without_receipt(self):
        service = FederatedRecoveryService()
        agg = SecureAggService(service)
        service.apply_recovery(TASK, ROUND, "操作员")
        result = agg.intake_recovered_report(TASK, ROUND, NODE_C, "sha256:e5f6", None)
        assert result["ok"] is False
        assert result["接收状态"] == "拒绝接收"
        assert result["retry_eligible"] is True

    def test_intake_accepts_with_valid_receipt(self):
        service = FederatedRecoveryService()
        agg = SecureAggService(service)
        receipt = service.apply_recovery(TASK, ROUND, "操作员")["receipt"]
        result = agg.intake_recovered_report(
            TASK, ROUND, NODE_C, "sha256:e5f6", receipt["回执编号"]
        )
        assert result["ok"] is True
        assert result["接收状态"] == "已接收"
        assert result["关联回执"] == receipt["回执编号"]
        assert result["round"]["缺失数量"] == 0

    def test_tracking_shows_receipts_and_retry_eligibility(self):
        service = FederatedRecoveryService()
        agg = SecureAggService(service)
        receipt = service.apply_recovery(TASK, ROUND, "操作员-林")["receipt"]
        # 交错：第三个节点正常回报 + 一个重复回报。
        service.submit_report(
            TASK, ROUND, NODE_C, "sha256:e5f6", receipt_id=receipt["回执编号"]
        )
        service.submit_report(
            TASK, ROUND, NODE_C, "sha256:dup", receipt_id=receipt["回执编号"]
        )
        tracking = agg.recovery_tracking(TASK, ROUND)
        assert tracking["ok"] is True
        # 关联的旧回执可查，且已随轮次收口被核销。
        assert len(tracking["关联回执"]) == 1
        assert tracking["关联回执"][0]["回执编号"] == receipt["回执编号"]
        assert tracking["关联回执"][0]["回执状态"] == "已核销"
        conclusions = {item["节点"]: item for item in tracking["节点回报明细"]}
        assert conclusions[NODE_C]["处理结论"] == RECEIPT_ACCEPTED
        assert conclusions[NODE_C]["重试资格"] is False
        assert conclusions[NODE_A]["处理结论"] == RECEIPT_ACCEPTED
        assert conclusions[NODE_B]["处理结论"] == RECEIPT_ACCEPTED
        # 轮次视角：三节点齐全、无缺失。
        assert tracking["轮次"]["缺失数量"] == 0

    def test_tracking_marks_retryable_after_rejected_intake(self):
        service = FederatedRecoveryService()
        agg = SecureAggService(service)
        service.apply_recovery(TASK, ROUND, "操作员")
        # 无回执的授权失败应保留重试资格。
        rejected = agg.intake_recovered_report(TASK, ROUND, NODE_C, "sha256:e5f6", None)
        assert rejected["retry_eligible"] is True
        tracking = agg.recovery_tracking(TASK, ROUND)
        pending = [item for item in tracking["节点回报明细"] if item["节点"] == NODE_C][0]
        assert pending["处理结论"] == "待回报"
        assert pending["重试资格"] is True


# ================================================================
# 5. 题设完整链路：两确认 -> 恢复 -> 交错第三节点与重复 -> 双页核对
# ================================================================

class TestEndToEndScenario:
    def test_full_interleaved_flow(self):
        service = FederatedRecoveryService()
        agg = SecureAggService(service)

        # 初始：3 节点中 2 个已确认，缺 1 个。
        overview = service.round_overview(TASK, ROUND)
        assert overview["已确认数量"] == 2 and overview["缺失数量"] == 1

        # 操作员选定第 7 轮发起恢复申请，拿到恢复回执。
        applied = service.apply_recovery(TASK, ROUND, "操作员-林")
        assert applied["ok"] is True
        receipt_id = applied["receipt"]["回执编号"]

        # 交错：先送一个无回执的回报被拒（可重试），再凭回执送第三个节点。
        rejected = service.submit_report(TASK, ROUND, NODE_C, "sha256:e5f6")
        assert rejected["receipt_status"] == RECEIPT_REJECTED
        accepted = service.submit_report(
            TASK, ROUND, NODE_C, "sha256:e5f6", receipt_id=receipt_id
        )
        assert accepted["ok"] is True

        # 再交错一个重复回报，必须幂等拦截。
        duplicate = service.submit_report(
            TASK, ROUND, NODE_C, "sha256:dup", receipt_id=receipt_id
        )
        assert duplicate["receipt_status"] == RECEIPT_DUPLICATE

        # 刷新后的训练页视角：轮次已聚合、3 节点确认、无缺失、回执可关联。
        train_view = service.round_overview(TASK, ROUND)
        assert train_view["轮次状态"] == ROUND_AGGREGATED
        assert train_view["已确认数量"] == 3
        assert train_view["缺失数量"] == 0
        assert train_view["最新恢复回执"] == receipt_id

        # 刷新后的聚合页视角：接纳明细、旧回执、重试资格一致。
        tracking = agg.recovery_tracking(TASK, ROUND)
        assert len(tracking["关联回执"]) == 1
        detail = {item["节点"]: item for item in tracking["节点回报明细"]}
        assert len(detail) == 3
        assert all(item["处理结论"] == RECEIPT_ACCEPTED for item in detail.values())
        assert all(item["重试资格"] is False for item in detail.values())
        # 已确认回报始终只有 3 条，没有任何节点被累计两次。
        confirmed = [r for r in service._round_reports(TASK, ROUND) if r["确认状态"] == "已确认"]
        assert len(confirmed) == 3

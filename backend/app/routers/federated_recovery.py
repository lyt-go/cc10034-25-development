"""联邦训练中断恢复 API。

路由固定前缀 /api/federated-recovery，必须先于通用 /api/{module} 资源路由注册，
避免被动态模块路径抢先匹配。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.federated_recovery import federated_recovery_service
from app.services.secure_agg import SecureAggService

router = APIRouter(prefix="/api/federated-recovery", tags=["联邦训练恢复"])
secure_agg_service = SecureAggService()


class RecoveryApplyPayload(BaseModel):
    task_id: str = Field(..., description="训练任务编号")
    round_no: int = Field(..., ge=1, description="需要恢复的训练轮次")
    operator: str | None = Field(default=None, description="操作员")


class ReportPayload(BaseModel):
    task_id: str
    round_no: int = Field(..., ge=1)
    node: str
    digest: str | None = None
    receipt_id: str | None = Field(default=None, description="恢复回执编号，恢复后回报必带")


@router.get("/rounds")
def list_rounds(task_id: str | None = None) -> dict[str, Any]:
    """轮次总览：谁的回报被接纳、当前还缺几个节点。"""
    return {"items": federated_recovery_service.list_rounds(task_id)}


@router.get("/rounds/{task_id}/{round_no}")
def round_overview(task_id: str, round_no: int) -> dict[str, Any]:
    return federated_recovery_service.round_overview(task_id, round_no)


@router.get("/receipts")
def list_receipts(task_id: str | None = None) -> dict[str, Any]:
    """恢复回执清单，供训练页与聚合页关联核对。"""
    return {"items": federated_recovery_service.list_receipts(task_id)}


@router.post("/apply")
def apply_recovery(payload: RecoveryApplyPayload) -> dict[str, Any]:
    """恢复申请：操作员选定轮次，服务端核验原回报与聚合检查点并回执。"""
    return federated_recovery_service.apply_recovery(
        payload.task_id, payload.round_no, payload.operator or ""
    )


@router.post("/reports")
def submit_report(payload: ReportPayload) -> dict[str, Any]:
    """节点回报提交：幂等防重、终态拦截、恢复授权都在服务内完成。"""
    return federated_recovery_service.submit_report(
        payload.task_id,
        payload.round_no,
        payload.node,
        payload.digest or "",
        receipt_id=payload.receipt_id,
    )


@router.post("/secure-agg/intake")
def secure_agg_intake(payload: ReportPayload) -> dict[str, Any]:
    """安全聚合凭恢复回执接收恢复后的回报。"""
    return secure_agg_service.intake_recovered_report(
        payload.task_id,
        payload.round_no,
        payload.node,
        payload.digest or "",
        payload.receipt_id,
    )


@router.get("/secure-agg/tracking/{task_id}/{round_no}")
def secure_agg_tracking(task_id: str, round_no: int) -> dict[str, Any]:
    """聚合页核对：接纳明细、关联旧回执、每个节点的重试资格。"""
    return secure_agg_service.recovery_tracking(task_id, round_no)


@router.post("/demo/reset")
def reset_demo() -> dict[str, Any]:
    """重置为题设三节点场景，方便重复演示与测试。"""
    return federated_recovery_service.reset_demo()

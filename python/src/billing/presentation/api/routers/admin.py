"""運用操作。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from billing.presentation.api.deps import ContainerDep
from billing.presentation.api.schemas import RenewalReportResponse

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/renewals", summary="更新バッチの実行")
def run_renewals(
    container: ContainerDep, limit: int = Query(default=100, ge=1, le=1000)
) -> RenewalReportResponse:
    """本来は cron や EventBridge から起動するもの。

    HTTP から叩けるようにしてあるのは、サンプルを触るときに時間を進めた効果を
    すぐ確かめられるようにするため。ユースケースは呼び出し元が誰かを知らないので、
    同じクラスを cron からも HTTP からも呼べる。
    """
    report = container.renew_due_subscriptions.execute(limit=limit)
    return RenewalReportResponse(
        renewed=report.renewed,
        invoiced=report.invoiced,
        payment_failed=report.payment_failed,
        terminated=report.terminated,
        canceled_for_nonpayment=report.canceled_for_nonpayment,
    )

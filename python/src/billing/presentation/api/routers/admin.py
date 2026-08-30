"""運用操作。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from billing.presentation.api.deps import ContainerDep
from billing.presentation.api.schemas import RenewalReportResponse, SettlementReportResponse

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
        charge_unreachable=report.charge_unreachable,
        terminated=report.terminated,
        canceled_for_nonpayment=report.canceled_for_nonpayment,
    )


@router.post("/unpaid-invoices/settlements", summary="決済結果の反映漏れを拾い直す")
def settle_unpaid_invoices(
    container: ContainerDep, limit: int = Query(default=100, ge=1, le=1000)
) -> SettlementReportResponse:
    """発行されたまま決着していない請求書に、もう一度決済を試みる。

    決済 API の呼び出しをトランザクションの外に出している以上、結果を反映する前に
    落ちる窓は必ず開く。この後始末が存在して初めて、その設計が成立する。
    """
    report = container.settle_unpaid_invoices.execute(limit=limit)
    return SettlementReportResponse(
        examined=report.examined,
        settled=report.settled,
        declined=report.declined,
        unreachable=report.unreachable,
    )

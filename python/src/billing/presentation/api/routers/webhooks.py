"""決済代行からの通知の受け口。"""

from __future__ import annotations

from fastapi import APIRouter

from billing.application.dto import PaymentNotification
from billing.presentation.api.deps import ContainerDep
from billing.presentation.api.schemas import InvoiceResponse, PaymentWebhookRequest

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/payments", summary="支払い結果の受け取り")
def record_payment(container: ContainerDep, body: PaymentWebhookRequest) -> InvoiceResponse:
    """同じ通知が何度届いても結果は変わらない。

    実運用では、ここに署名検証（Stripe なら Stripe-Signature ヘッダ）が入る。
    それは「この HTTP リクエストが本物か」という境界の関心であって、ユースケースの
    関心ではないので、この層で完結させる。
    """
    view = container.record_payment_result.execute(
        PaymentNotification(
            invoice_id=body.invoice_id,
            succeeded=body.succeeded,
            provider_reference=body.provider_reference,
            failure_reason=body.failure_reason,
        )
    )
    return InvoiceResponse.of(view)

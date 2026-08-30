"""契約の作成・参照・変更・解約。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header

from billing.application.dto import CancelCommand, ChangePlanCommand, SubscribeCommand
from billing.presentation.api.deps import ContainerDep
from billing.presentation.api.schemas import (
    CancelRequest,
    ChangePlanRequest,
    ChangePlanResponse,
    InvoiceResponse,
    ProrationResponse,
    SubscribeRequest,
    SubscribeResponse,
    SubscriptionResponse,
)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.post("", status_code=201, summary="新規契約")
def subscribe(container: ContainerDep, body: SubscribeRequest) -> SubscribeResponse:
    result = container.subscribe_to_plan.execute(
        SubscribeCommand(
            customer_id=body.customer_id,
            plan_id=body.plan_id,
            trial_days=body.trial_days,
        )
    )
    return SubscribeResponse(
        subscription=SubscriptionResponse.of(result.subscription),
        invoice=None if result.invoice is None else InvoiceResponse.of(result.invoice),
        payment_failed=result.payment_failed,
    )


@router.get("/{subscription_id}", summary="契約の参照")
def get_subscription(container: ContainerDep, subscription_id: str) -> SubscriptionResponse:
    return SubscriptionResponse.of(container.queries.get_subscription(subscription_id))


@router.post("/{subscription_id}/plan-changes", summary="プラン変更と日割り請求")
def change_plan(
    container: ContainerDep,
    subscription_id: str,
    body: ChangePlanRequest,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            description="同じ鍵での再送は二重に課金されない。クライアントが生成する",
        ),
    ],
) -> ChangePlanResponse:
    # 冪等キーを任意ではなく必須にしている。「再送しても安全な API」は、クライアントが
    # 気を利かせたときだけ成立する性質であってはならない。
    result = container.change_plan.execute(
        ChangePlanCommand(
            subscription_id=subscription_id,
            new_plan_id=body.new_plan_id,
            idempotency_key=idempotency_key,
        )
    )
    return ChangePlanResponse(
        subscription=SubscriptionResponse.of(result.subscription),
        proration=ProrationResponse.of(result.proration),
        invoice=InvoiceResponse.of(result.invoice),
    )


@router.post("/{subscription_id}/cancellation", summary="解約（既定は期末解約）")
def cancel(
    container: ContainerDep, subscription_id: str, body: CancelRequest
) -> SubscriptionResponse:
    view = container.cancel_subscription.execute(
        CancelCommand(subscription_id=subscription_id, immediately=body.immediately)
    )
    return SubscriptionResponse.of(view)

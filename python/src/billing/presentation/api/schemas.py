"""HTTP のリクエスト・レスポンス形式。

Pydantic モデルはこのファイルの外に出さない。ユースケースは application/dto.py の
dataclass だけを受け取り、返す。境界で 1 回変換する手間を引き受ける代わりに、
「API の互換性のためにフィールド名を変えたい」がドメインに波及しなくなる。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from billing.application.dto import (
    InvoiceView,
    MoneyView,
    PlanView,
    ProrationView,
    SubscriptionView,
)


class MoneyResponse(BaseModel):
    amount: int = Field(description="通貨の最小単位。日本円なら円、米ドルならセント")
    currency: str

    @classmethod
    def of(cls, view: MoneyView) -> MoneyResponse:
        return cls(amount=view.amount, currency=view.currency)


class PlanResponse(BaseModel):
    id: str
    name: str
    price: MoneyResponse
    interval: str

    @classmethod
    def of(cls, view: PlanView) -> PlanResponse:
        return cls(
            id=view.id,
            name=view.name,
            price=MoneyResponse.of(view.price),
            interval=view.interval,
        )


class SubscribeRequest(BaseModel):
    customer_id: str
    plan_id: str
    trial_days: int | None = Field(default=None, ge=1, le=365)


class ChangePlanRequest(BaseModel):
    new_plan_id: str


class CancelRequest(BaseModel):
    immediately: bool = False


class SubscriptionResponse(BaseModel):
    id: str
    customer_id: str
    plan_id: str
    status: str
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    trial_end: datetime | None

    @classmethod
    def of(cls, view: SubscriptionView) -> SubscriptionResponse:
        return cls(
            id=view.id,
            customer_id=view.customer_id,
            plan_id=view.plan_id,
            status=view.status,
            current_period_start=view.current_period_start,
            current_period_end=view.current_period_end,
            cancel_at_period_end=view.cancel_at_period_end,
            trial_end=view.trial_end,
        )


class InvoiceLineResponse(BaseModel):
    description: str
    amount: MoneyResponse


class InvoiceResponse(BaseModel):
    id: str
    subscription_id: str
    status: str
    total: MoneyResponse
    lines: list[InvoiceLineResponse]
    issued_at: datetime | None
    paid_at: datetime | None

    @classmethod
    def of(cls, view: InvoiceView) -> InvoiceResponse:
        return cls(
            id=view.id,
            subscription_id=view.subscription_id,
            status=view.status,
            total=MoneyResponse.of(view.total),
            lines=[
                InvoiceLineResponse(description=description, amount=MoneyResponse.of(amount))
                for description, amount in view.lines
            ],
            issued_at=view.issued_at,
            paid_at=view.paid_at,
        )


class ProrationResponse(BaseModel):
    credit: MoneyResponse
    charge: MoneyResponse
    net: MoneyResponse

    @classmethod
    def of(cls, view: ProrationView) -> ProrationResponse:
        return cls(
            credit=MoneyResponse.of(view.credit),
            charge=MoneyResponse.of(view.charge),
            net=MoneyResponse.of(view.net),
        )


class SubscribeResponse(BaseModel):
    subscription: SubscriptionResponse
    invoice: InvoiceResponse | None
    payment_failed: bool


class ChangePlanResponse(BaseModel):
    subscription: SubscriptionResponse
    proration: ProrationResponse
    invoice: InvoiceResponse


class PaymentWebhookRequest(BaseModel):
    invoice_id: str
    succeeded: bool
    provider_reference: str | None = None
    failure_reason: str | None = None


class RenewalReportResponse(BaseModel):
    renewed: int
    invoiced: int
    payment_failed: int = Field(description="決済代行がはっきり拒否した件数")
    charge_unreachable: int = Field(
        description="決済代行に届かず結果が分からなかった件数。請求書は open のまま残る"
    )
    terminated: int
    canceled_for_nonpayment: int


class SettlementReportResponse(BaseModel):
    examined: int
    settled: int
    declined: int
    unreachable: int


class ErrorResponse(BaseModel):
    error: str
    detail: str

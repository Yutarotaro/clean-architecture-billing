"""ユースケースの入出力。

presentation 層の Pydantic モデルをここに持ち込まない。ユースケースは HTTP 経由でも
CLI からでもバッチからでも呼ばれうるので、その形は「FastAPI が読みやすい形」であっては
ならない。素の dataclass にしておけば、どこから呼んでも同じ。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from billing.domain.invoice import Invoice
from billing.domain.plan import Plan
from billing.domain.proration import Proration
from billing.domain.subscription import Subscription


@dataclass(frozen=True, slots=True)
class SubscribeCommand:
    customer_id: str
    plan_id: str
    trial_days: int | None = None


@dataclass(frozen=True, slots=True)
class ChangePlanCommand:
    subscription_id: str
    new_plan_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class CancelCommand:
    subscription_id: str
    immediately: bool = False


@dataclass(frozen=True, slots=True)
class PaymentNotification:
    """決済代行から届く支払い結果の通知。"""

    invoice_id: str
    succeeded: bool
    provider_reference: str | None = None
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class MoneyView:
    amount: int
    currency: str


@dataclass(frozen=True, slots=True)
class PlanView:
    id: str
    name: str
    price: MoneyView
    interval: str

    @classmethod
    def of(cls, plan: Plan) -> PlanView:
        return cls(
            id=plan.id,
            name=plan.name,
            price=MoneyView(plan.price.amount, plan.price.currency),
            interval=str(plan.interval),
        )


@dataclass(frozen=True, slots=True)
class SubscriptionView:
    id: str
    customer_id: str
    plan_id: str
    status: str
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    trial_end: datetime | None

    @classmethod
    def of(cls, subscription: Subscription) -> SubscriptionView:
        return cls(
            id=subscription.id,
            customer_id=subscription.customer_id,
            plan_id=subscription.plan_id,
            status=str(subscription.status),
            current_period_start=subscription.current_period.start,
            current_period_end=subscription.current_period.end,
            cancel_at_period_end=subscription.cancel_at_period_end,
            trial_end=subscription.trial_end,
        )


@dataclass(frozen=True, slots=True)
class InvoiceView:
    id: str
    subscription_id: str
    status: str
    total: MoneyView
    lines: list[tuple[str, MoneyView]]
    issued_at: datetime | None
    paid_at: datetime | None

    @classmethod
    def of(cls, invoice: Invoice) -> InvoiceView:
        return cls(
            id=invoice.id,
            subscription_id=invoice.subscription_id,
            status=str(invoice.status),
            total=MoneyView(invoice.total.amount, invoice.total.currency),
            lines=[
                (line.description, MoneyView(line.amount.amount, line.amount.currency))
                for line in invoice.lines
            ],
            issued_at=invoice.issued_at,
            paid_at=invoice.paid_at,
        )


@dataclass(frozen=True, slots=True)
class ProrationView:
    credit: MoneyView
    charge: MoneyView
    net: MoneyView

    @classmethod
    def of(cls, proration: Proration) -> ProrationView:
        return cls(
            credit=MoneyView(proration.credit.amount, proration.credit.currency),
            charge=MoneyView(proration.charge.amount, proration.charge.currency),
            net=MoneyView(proration.net.amount, proration.net.currency),
        )


@dataclass(frozen=True, slots=True)
class SubscribeResult:
    subscription: SubscriptionView
    invoice: InvoiceView | None
    payment_failed: bool = False


@dataclass(frozen=True, slots=True)
class ChangePlanResult:
    subscription: SubscriptionView
    proration: ProrationView
    invoice: InvoiceView
    """プラン変更は差額が 0 以下でも必ず請求書を残すので、常に存在する。"""


@dataclass(frozen=True, slots=True)
class RenewalReport:
    """バッチ 1 回ぶんの結果。"""

    renewed: int = 0
    invoiced: int = 0
    payment_failed: int = 0
    terminated: int = 0
    canceled_for_nonpayment: int = 0

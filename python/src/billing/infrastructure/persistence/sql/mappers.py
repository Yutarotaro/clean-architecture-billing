"""行とドメインオブジェクトの相互変換。

このモジュールが存在することが、このプロジェクトの立場そのものである。ORM に
ドメインクラスを直接マップさせれば mappers.py は要らなくなるが、そのかわり
ドメインクラスが ORM の基底クラスを継承し、遅延ロードのために属性アクセスが
DB アクセスに化ける。ここに退屈な変換コードを書くのは、その代償を払わないため。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from billing.domain.ids import CustomerId, InvoiceId, PlanId, SubscriptionId
from billing.domain.invoice import Invoice, InvoiceLine, InvoiceStatus
from billing.domain.money import Money
from billing.domain.period import BillingPeriod
from billing.domain.plan import BillingInterval, Plan
from billing.domain.subscription import Subscription, SubscriptionStatus
from billing.infrastructure.persistence.timestamps import from_iso, to_iso


def _require(value: datetime | None, field: str) -> datetime:
    if value is None:
        raise ValueError(f"{field} must not be null in the database")
    return value


def plan_to_row(plan: Plan) -> dict[str, Any]:
    return {
        "id": str(plan.id),
        "name": plan.name,
        "price_amount": plan.price.amount,
        "price_currency": plan.price.currency,
        "interval": str(plan.interval),
    }


def row_to_plan(row: Any) -> Plan:
    return Plan(
        id=PlanId(row.id),
        name=row.name,
        price=Money(row.price_amount, row.price_currency),
        interval=BillingInterval(row.interval),
    )


def subscription_to_row(subscription: Subscription) -> dict[str, Any]:
    return {
        "id": str(subscription.id),
        "customer_id": str(subscription.customer_id),
        "plan_id": str(subscription.plan_id),
        "status": str(subscription.status),
        "period_start": to_iso(subscription.current_period.start),
        "period_end": to_iso(subscription.current_period.end),
        "cancel_at_period_end": subscription.cancel_at_period_end,
        "past_due_since": to_iso(subscription.past_due_since),
        "canceled_at": to_iso(subscription.canceled_at),
        "trial_end": to_iso(subscription.trial_end),
    }


def row_to_subscription(row: Any) -> Subscription:
    return Subscription(
        id=SubscriptionId(row.id),
        customer_id=CustomerId(row.customer_id),
        plan_id=PlanId(row.plan_id),
        status=SubscriptionStatus(row.status),
        current_period=BillingPeriod(
            _require(from_iso(row.period_start), "period_start"),
            _require(from_iso(row.period_end), "period_end"),
        ),
        cancel_at_period_end=bool(row.cancel_at_period_end),
        past_due_since=from_iso(row.past_due_since),
        canceled_at=from_iso(row.canceled_at),
        trial_end=from_iso(row.trial_end),
    )


def invoice_to_row(invoice: Invoice) -> dict[str, Any]:
    return {
        "id": str(invoice.id),
        "customer_id": str(invoice.customer_id),
        "subscription_id": str(invoice.subscription_id),
        "status": str(invoice.status),
        "currency": invoice.currency,
        "issued_at": to_iso(invoice.issued_at),
        "paid_at": to_iso(invoice.paid_at),
        "idempotency_key": invoice.idempotency_key,
    }


def invoice_line_rows(invoice: Invoice) -> list[dict[str, Any]]:
    return [
        {
            "invoice_id": str(invoice.id),
            "position": position,
            "description": line.description,
            "amount": line.amount.amount,
            "currency": line.amount.currency,
        }
        for position, line in enumerate(invoice.lines)
    ]


def row_to_invoice(row: Any, line_rows: list[Any]) -> Invoice:
    return Invoice(
        id=InvoiceId(row.id),
        customer_id=CustomerId(row.customer_id),
        subscription_id=SubscriptionId(row.subscription_id),
        lines=[
            InvoiceLine(description=line.description, amount=Money(line.amount, line.currency))
            for line in sorted(line_rows, key=lambda line: line.position)
        ],
        currency=row.currency,
        status=InvoiceStatus(row.status),
        issued_at=from_iso(row.issued_at),
        paid_at=from_iso(row.paid_at),
        idempotency_key=row.idempotency_key,
    )

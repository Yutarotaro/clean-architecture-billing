"""項目とドメインオブジェクトの相互変換。

SQL 版との違いがそのままデータモデルの違いになっている。請求明細は、SQL では
別テーブルへの外部キーだが、ここでは項目に埋め込んだリストである。「請求書を読む」が
1 回の GetItem で済むのは DynamoDB 側の利点で、逆に「明細だけを横断集計する」は
苦手になる。どちらを選んでもドメインモデルは変わらない、というのがこの層の役目。
"""

from __future__ import annotations

from typing import Any

from billing.domain.ids import CustomerId, InvoiceId, PlanId, SubscriptionId
from billing.domain.invoice import Invoice, InvoiceLine, InvoiceStatus
from billing.domain.money import Money
from billing.domain.period import BillingPeriod
from billing.domain.plan import BillingInterval, Plan
from billing.domain.subscription import Subscription, SubscriptionStatus
from billing.infrastructure.persistence.dynamo.schema import (
    LIVE_PARTITION,
    PAST_DUE_PARTITION,
    customer_invoices_partition,
    invoice_key,
    plan_key,
    subscription_key,
)
from billing.infrastructure.persistence.timestamps import (
    from_iso,
    require_from_iso,
    require_iso,
    to_iso,
)

#: 更新バッチの対象になりうる状態。
_LIVE_STATUSES = frozenset(
    {SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING, SubscriptionStatus.PAST_DUE}
)


def _without_none(item: dict[str, Any]) -> dict[str, Any]:
    """値が None の属性を落とす。

    DynamoDB では「属性が存在しない」ことに意味がある。null を入れるのではなく
    属性ごと消すことで、GSI に載せない（sparse index）という制御ができる。
    """
    return {key: value for key, value in item.items() if value is not None}


def plan_to_item(plan: Plan, *, version: int) -> dict[str, Any]:
    return {
        **plan_key(plan.id),
        "type": "plan",
        "id": str(plan.id),
        "name": plan.name,
        "price_amount": plan.price.amount,
        "price_currency": plan.price.currency,
        "interval": str(plan.interval),
        "version": version,
    }


def item_to_plan(item: dict[str, Any]) -> Plan:
    return Plan(
        id=PlanId(item["id"]),
        name=item["name"],
        price=Money(int(item["price_amount"]), item["price_currency"]),
        interval=BillingInterval(item["interval"]),
    )


def subscription_to_item(subscription: Subscription, *, version: int) -> dict[str, Any]:
    is_live = subscription.status in _LIVE_STATUSES
    is_past_due = subscription.status is SubscriptionStatus.PAST_DUE
    return _without_none(
        {
            **subscription_key(subscription.id),
            "type": "subscription",
            "id": str(subscription.id),
            "customer_id": str(subscription.customer_id),
            "plan_id": str(subscription.plan_id),
            "status": str(subscription.status),
            "period_start": require_iso(subscription.current_period.start, field="period_start"),
            "period_end": require_iso(subscription.current_period.end, field="period_end"),
            "cancel_at_period_end": subscription.cancel_at_period_end,
            "past_due_since": to_iso(subscription.past_due_since),
            "canceled_at": to_iso(subscription.canceled_at),
            "trial_end": to_iso(subscription.trial_end),
            # 解約済みになった瞬間にこの 2 つの属性が消え、索引から自動的に外れる。
            # 「解約済みを除外する」条件をクエリに書く必要がなくなる。
            "gsi1pk": LIVE_PARTITION if is_live else None,
            "gsi1sk": require_iso(subscription.current_period.end, field="period_end")
            if is_live
            else None,
            "gsi2pk": PAST_DUE_PARTITION if is_past_due else None,
            "gsi2sk": to_iso(subscription.past_due_since) if is_past_due else None,
            "version": version,
        }
    )


def item_to_subscription(item: dict[str, Any]) -> Subscription:
    return Subscription(
        id=SubscriptionId(item["id"]),
        customer_id=CustomerId(item["customer_id"]),
        plan_id=PlanId(item["plan_id"]),
        status=SubscriptionStatus(item["status"]),
        current_period=BillingPeriod(
            require_from_iso(item["period_start"], field="period_start"),
            require_from_iso(item["period_end"], field="period_end"),
        ),
        cancel_at_period_end=bool(item["cancel_at_period_end"]),
        past_due_since=_optional_time(item, "past_due_since"),
        canceled_at=_optional_time(item, "canceled_at"),
        trial_end=_optional_time(item, "trial_end"),
    )


def invoice_to_item(invoice: Invoice, *, version: int) -> dict[str, Any]:
    issued_at = to_iso(invoice.issued_at)
    return _without_none(
        {
            **invoice_key(invoice.id),
            "type": "invoice",
            "id": str(invoice.id),
            "customer_id": str(invoice.customer_id),
            "subscription_id": str(invoice.subscription_id),
            "status": str(invoice.status),
            "currency": invoice.currency,
            "issued_at": issued_at,
            "paid_at": to_iso(invoice.paid_at),
            "idempotency_key": invoice.idempotency_key,
            "lines": [
                {
                    "description": line.description,
                    "amount": line.amount.amount,
                    "currency": line.amount.currency,
                }
                for line in invoice.lines
            ],
            "gsi2pk": customer_invoices_partition(invoice.customer_id),
            "gsi2sk": issued_at,
            "version": version,
        }
    )


def item_to_invoice(item: dict[str, Any]) -> Invoice:
    return Invoice(
        id=InvoiceId(item["id"]),
        customer_id=CustomerId(item["customer_id"]),
        subscription_id=SubscriptionId(item["subscription_id"]),
        lines=[
            InvoiceLine(
                description=line["description"],
                amount=Money(int(line["amount"]), line["currency"]),
            )
            for line in item.get("lines", [])
        ],
        currency=item["currency"],
        status=InvoiceStatus(item["status"]),
        issued_at=_optional_time(item, "issued_at"),
        paid_at=_optional_time(item, "paid_at"),
        idempotency_key=item.get("idempotency_key"),
    )


def _optional_time(item: dict[str, Any], field: str) -> Any:
    return from_iso(item.get(field))

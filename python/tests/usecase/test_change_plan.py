"""プラン変更ユースケースのテスト。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from billing.application.dto import ChangePlanCommand, SubscribeCommand
from billing.application.errors import ConflictingRequest
from billing.infrastructure.clock import FixedClock
from billing.infrastructure.payment.fake_gateway import FakePaymentGateway
from billing.presentation.container import Container

MID_JANUARY = datetime(2026, 1, 16, 12, tzinfo=UTC)


def subscribe(container: Container, plan_id: str = "basic", customer: str = "cus-1") -> str:
    result = container.subscribe_to_plan.execute(
        SubscribeCommand(customer_id=customer, plan_id=plan_id)
    )
    return result.subscription.id


def test_upgrading_mid_period_charges_the_difference(
    container: Container, clock: FixedClock, gateway: FakePaymentGateway
) -> None:
    """1 月の折り返しで Basic から Pro へ。差額 1,000 円が即時請求される。"""
    subscription_id = subscribe(container)
    clock.set(MID_JANUARY)

    result = container.change_plan.execute(
        ChangePlanCommand(
            subscription_id=subscription_id,
            new_plan_id="pro",
            idempotency_key="change-1",
        )
    )

    assert result.subscription.plan_id == "pro"
    assert result.proration.credit.amount == 500
    assert result.proration.charge.amount == 1_500
    assert result.proration.net.amount == 1_000
    assert result.invoice is not None
    assert result.invoice.total.amount == 1_000
    assert result.invoice.status == "paid"
    # 初回請求 1,000 円 + 差額 1,000 円
    assert gateway.settled_amount == 2_000


def test_the_proration_invoice_shows_both_sides(container: Container, clock: FixedClock) -> None:
    """合計だけでなく内訳を残す。問い合わせに答えられない請求書は不良品である。"""
    subscription_id = subscribe(container)
    clock.set(MID_JANUARY)

    result = container.change_plan.execute(
        ChangePlanCommand(
            subscription_id=subscription_id, new_plan_id="pro", idempotency_key="change-1"
        )
    )

    assert result.invoice is not None
    descriptions = [description for description, _ in result.invoice.lines]
    amounts = [amount.amount for _, amount in result.invoice.lines]
    assert descriptions == ["Basic 未使用分", "Pro 残期間分"]
    assert amounts == [-500, 1_500]


def test_downgrading_does_not_charge(
    container: Container, clock: FixedClock, gateway: FakePaymentGateway
) -> None:
    """差額が負なら請求書は作らない。新しい料金は次の期間から効く。"""
    subscription_id = subscribe(container, "pro")
    clock.set(MID_JANUARY)

    result = container.change_plan.execute(
        ChangePlanCommand(
            subscription_id=subscription_id, new_plan_id="basic", idempotency_key="change-1"
        )
    )

    assert result.proration.net.amount == -1_000
    assert result.invoice is None
    assert gateway.settled_amount == 3_000  # 初回請求のぶんだけ


def test_replaying_the_same_request_does_not_charge_twice(
    container: Container, clock: FixedClock, gateway: FakePaymentGateway
) -> None:
    """ネットワークの再送で二重に課金しない。

    クライアントは「タイムアウトしたのでもう一度送る」を必ずやる。冪等キーが
    効いていないと、その 1 回がそのまま二重課金になる。
    """
    subscription_id = subscribe(container)
    clock.set(MID_JANUARY)
    command = ChangePlanCommand(
        subscription_id=subscription_id, new_plan_id="pro", idempotency_key="change-1"
    )

    first = container.change_plan.execute(command)
    second = container.change_plan.execute(command)

    assert first.invoice is not None
    assert second.invoice is not None
    assert first.invoice.id == second.invoice.id
    assert second.proration.net.amount == 1_000
    assert gateway.settled_amount == 2_000


def test_reusing_a_key_for_another_subscription_is_rejected(
    container: Container, clock: FixedClock
) -> None:
    first = subscribe(container)
    second = subscribe(container, customer="cus-2")
    clock.set(MID_JANUARY)

    container.change_plan.execute(
        ChangePlanCommand(subscription_id=first, new_plan_id="pro", idempotency_key="shared")
    )

    with pytest.raises(ConflictingRequest):
        container.change_plan.execute(
            ChangePlanCommand(subscription_id=second, new_plan_id="pro", idempotency_key="shared")
        )

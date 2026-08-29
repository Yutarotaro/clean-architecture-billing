"""新規契約ユースケースのテスト。

``container`` フィクスチャ経由で動くので、このファイルの各テストは memory・SQLite・
DynamoDB の 3 通りで実行される。ユースケースのコードには 1 箇所も分岐がない。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from billing.application.charging import UnitOfWorkFactory
from billing.application.dto import SubscribeCommand
from billing.application.errors import EntityNotFound
from billing.domain.money import Money
from billing.infrastructure.clock import FixedClock
from billing.infrastructure.ids import SequentialIdGenerator
from billing.infrastructure.payment.fake_gateway import FakePaymentGateway
from billing.presentation.container import Container


def test_subscribing_without_trial_charges_immediately(
    container: Container, gateway: FakePaymentGateway
) -> None:
    result = container.subscribe_to_plan.execute(
        SubscribeCommand(customer_id="cus-1", plan_id="pro")
    )

    assert result.subscription.status == "active"
    assert result.invoice is not None
    assert result.invoice.total.amount == 3_000
    assert result.invoice.status == "paid"
    assert result.payment_failed is False
    assert len(gateway.attempts) == 1
    assert gateway.attempts[0].amount == Money(3_000)


def test_the_first_invoice_covers_exactly_one_billing_period(container: Container) -> None:
    result = container.subscribe_to_plan.execute(
        SubscribeCommand(customer_id="cus-1", plan_id="basic")
    )

    assert result.subscription.current_period_start == datetime(2026, 1, 1, tzinfo=UTC)
    assert result.subscription.current_period_end == datetime(2026, 2, 1, tzinfo=UTC)


def test_subscribing_with_trial_does_not_charge(
    container: Container, gateway: FakePaymentGateway
) -> None:
    result = container.subscribe_to_plan.execute(
        SubscribeCommand(customer_id="cus-1", plan_id="pro", trial_days=14)
    )

    assert result.subscription.status == "trialing"
    assert result.invoice is None
    assert gateway.attempts == []
    assert result.subscription.trial_end == datetime(2026, 1, 15, tzinfo=UTC)


def test_a_declined_card_leaves_the_subscription_past_due(
    uow_factory: UnitOfWorkFactory,
    clock: FixedClock,
    ids: SequentialIdGenerator,
) -> None:
    """決済が失敗しても契約自体は作られ、猶予期間に入る。

    ここだけコンテナを手で組み直しているのは、常に失敗する決済代行を注入するため。
    差し替えているのは gateway 1 つで、それ以外は本番と同じ配線である。
    """
    declining = FakePaymentGateway(decline_when=lambda _: True)
    container = Container(uow_factory=uow_factory, clock=clock, ids=ids, gateway=declining)
    container.seed_plans()

    result = container.subscribe_to_plan.execute(
        SubscribeCommand(customer_id="cus-1", plan_id="basic")
    )

    assert result.payment_failed is True
    assert result.subscription.status == "past_due"
    assert result.invoice is not None
    assert result.invoice.status == "open"


def test_subscribing_to_an_unknown_plan_fails(container: Container) -> None:
    with pytest.raises(EntityNotFound):
        container.subscribe_to_plan.execute(SubscribeCommand(customer_id="cus-1", plan_id="nope"))

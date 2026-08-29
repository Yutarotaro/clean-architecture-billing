"""決済結果の受け取りのテスト。

webhook は「少なくとも 1 回」しか保証されない。同じ通知が複数回届くのは通常の
動作であり、それを前提に書かれていないコードは本番で必ず壊れる。
"""

from __future__ import annotations

import pytest

from billing.application.charging import UnitOfWorkFactory
from billing.application.dto import PaymentNotification, SubscribeCommand
from billing.application.errors import EntityNotFound
from billing.infrastructure.clock import FixedClock
from billing.infrastructure.ids import SequentialIdGenerator
from billing.infrastructure.payment.fake_gateway import FakePaymentGateway
from billing.presentation.container import Container


def failing_container(
    uow_factory: UnitOfWorkFactory, clock: FixedClock, ids: SequentialIdGenerator
) -> Container:
    """初回決済が必ず失敗するコンテナ。past_due の契約を作るために使う。"""
    container = Container(
        uow_factory=uow_factory,
        clock=clock,
        ids=ids,
        gateway=FakePaymentGateway(decline_when=lambda _: True),
    )
    container.seed_plans()
    return container


def test_a_late_success_notification_settles_the_invoice(
    uow_factory: UnitOfWorkFactory, clock: FixedClock, ids: SequentialIdGenerator
) -> None:
    """同期の決済には失敗したが、後から成功通知が届いた場合。"""
    container = failing_container(uow_factory, clock, ids)
    result = container.subscribe_to_plan.execute(
        SubscribeCommand(customer_id="cus-1", plan_id="basic")
    )
    assert result.invoice is not None
    assert result.subscription.status == "past_due"

    view = container.record_payment_result.execute(
        PaymentNotification(invoice_id=result.invoice.id, succeeded=True, provider_reference="ch_x")
    )

    assert view.status == "paid"
    assert container.queries.get_subscription(result.subscription.id).status == "active"


def test_the_same_notification_twice_changes_nothing(
    uow_factory: UnitOfWorkFactory, clock: FixedClock, ids: SequentialIdGenerator
) -> None:
    container = failing_container(uow_factory, clock, ids)
    result = container.subscribe_to_plan.execute(
        SubscribeCommand(customer_id="cus-1", plan_id="basic")
    )
    assert result.invoice is not None
    notification = PaymentNotification(invoice_id=result.invoice.id, succeeded=True)

    first = container.record_payment_result.execute(notification)
    second = container.record_payment_result.execute(notification)

    assert first.paid_at == second.paid_at
    assert second.status == "paid"


def test_a_failure_notification_moves_the_subscription_to_past_due(
    container: Container,
) -> None:
    result = container.subscribe_to_plan.execute(
        SubscribeCommand(customer_id="cus-1", plan_id="basic")
    )
    assert result.invoice is not None
    assert result.subscription.status == "active"

    container.record_payment_result.execute(
        PaymentNotification(
            invoice_id=result.invoice.id, succeeded=False, failure_reason="insufficient_funds"
        )
    )

    assert container.queries.get_subscription(result.subscription.id).status == "past_due"


def test_a_notification_for_an_unknown_invoice_is_rejected(container: Container) -> None:
    with pytest.raises(EntityNotFound):
        container.record_payment_result.execute(
            PaymentNotification(invoice_id="inv-does-not-exist", succeeded=True)
        )

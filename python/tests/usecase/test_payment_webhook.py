"""決済結果の受け取りのテスト。

webhook は「少なくとも 1 回」しか保証されない。同じ通知が複数回届くのは通常の
動作であり、それを前提に書かれていないコードは本番で必ず壊れる。
"""

from __future__ import annotations

from datetime import UTC, datetime

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


def test_a_stale_failure_notification_is_ignored(container: Container) -> None:
    """支払い済みの請求書に遅れて届いた失敗通知は、契約に影響しない。

    webhook は配信順序を保証しない。成功のあとに失敗が届くのは異常ではなく通常の
    動作である。ここで past_due に落とすと、支払い済みの顧客が「未払い」として
    猶予期間ののちに解約される。請求書は paid のまま、契約だけが canceled になり、
    どこにも警告は出ない。
    """
    result = container.subscribe_to_plan.execute(
        SubscribeCommand(customer_id="cus-1", plan_id="basic")
    )
    assert result.invoice is not None
    assert result.invoice.status == "paid"
    assert result.subscription.status == "active"

    container.record_payment_result.execute(
        PaymentNotification(
            invoice_id=result.invoice.id, succeeded=False, failure_reason="insufficient_funds"
        )
    )

    assert container.queries.get_subscription(result.subscription.id).status == "active"
    assert [invoice.status for invoice in container.queries.list_invoices("cus-1")] == ["paid"]


def test_a_stale_failure_notification_does_not_lead_to_cancellation(
    container: Container, clock: FixedClock
) -> None:
    """遅れて届いた失敗通知のあとにバッチを回しても、解約されない。

    これが実際に顧客へ影響が出る経路。past_due になっただけでは気づかれず、
    14 日後の更新バッチで初めて解約される。
    """
    result = container.subscribe_to_plan.execute(
        SubscribeCommand(customer_id="cus-1", plan_id="basic")
    )
    assert result.invoice is not None
    container.record_payment_result.execute(
        PaymentNotification(invoice_id=result.invoice.id, succeeded=False)
    )

    clock.set(datetime(2026, 1, 20, tzinfo=UTC))
    report = container.renew_due_subscriptions.execute()

    assert report.canceled_for_nonpayment == 0
    assert container.queries.get_subscription(result.subscription.id).status == "active"


def test_a_notification_for_an_unknown_invoice_is_rejected(container: Container) -> None:
    with pytest.raises(EntityNotFound):
        container.record_payment_result.execute(
            PaymentNotification(invoice_id="inv-does-not-exist", succeeded=True)
        )

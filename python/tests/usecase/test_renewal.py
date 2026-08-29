"""更新バッチのテスト。

時計を進めるだけで「1 か月後」「猶予期間の 14 日後」を再現できる。実時間に依存する
テストは書かない。
"""

from __future__ import annotations

from datetime import UTC, datetime

from billing.application.charging import UnitOfWorkFactory
from billing.application.dto import CancelCommand, SubscribeCommand
from billing.domain.ids import SubscriptionId
from billing.infrastructure.clock import FixedClock
from billing.infrastructure.ids import SequentialIdGenerator
from billing.infrastructure.payment.fake_gateway import FakePaymentGateway
from billing.presentation.container import Container

FEB = datetime(2026, 2, 1, tzinfo=UTC)
MAR = datetime(2026, 3, 1, tzinfo=UTC)


def subscribe(container: Container, plan_id: str = "basic", customer: str = "cus-1") -> str:
    return container.subscribe_to_plan.execute(
        SubscribeCommand(customer_id=customer, plan_id=plan_id)
    ).subscription.id


def test_renewal_advances_the_period_and_issues_an_invoice(
    container: Container, clock: FixedClock, gateway: FakePaymentGateway
) -> None:
    subscription_id = subscribe(container)
    clock.set(FEB)

    report = container.renew_due_subscriptions.execute()

    assert report.renewed == 1
    assert report.invoiced == 1
    assert report.payment_failed == 0

    view = container.queries.get_subscription(subscription_id)
    assert view.current_period_start == FEB
    assert view.current_period_end == MAR
    assert gateway.settled_amount == 2_000  # 1 月分 + 2 月分


def test_renewal_is_a_no_op_before_the_period_ends(container: Container, clock: FixedClock) -> None:
    subscribe(container)
    clock.set(datetime(2026, 1, 20, tzinfo=UTC))

    report = container.renew_due_subscriptions.execute()

    assert report.renewed == 0
    assert report.invoiced == 0


def test_running_the_batch_twice_does_not_double_charge(
    container: Container, clock: FixedClock, gateway: FakePaymentGateway
) -> None:
    """バッチが二重に起動されても、請求は 1 回で済む。

    期間が進んだあとは ``is_due`` が偽になるので、2 回目は対象にすらならない。
    仮に対象になったとしても、請求書の冪等キーが期間の開始時刻から決まるため、
    同じ期間に 2 通目は作られない。
    """
    subscribe(container)
    clock.set(FEB)

    first = container.renew_due_subscriptions.execute()
    second = container.renew_due_subscriptions.execute()

    assert first.renewed == 1
    assert second.renewed == 0
    assert gateway.settled_amount == 2_000


def test_scheduled_cancellation_terminates_at_renewal(
    container: Container, clock: FixedClock, gateway: FakePaymentGateway
) -> None:
    subscription_id = subscribe(container)
    container.cancel_subscription.execute(CancelCommand(subscription_id=subscription_id))
    clock.set(FEB)

    report = container.renew_due_subscriptions.execute()

    assert report.terminated == 1
    assert report.invoiced == 0
    assert container.queries.get_subscription(subscription_id).status == "canceled"
    assert gateway.settled_amount == 1_000  # 1 月分だけ


def test_trial_converts_into_a_paid_period(
    container: Container, clock: FixedClock, gateway: FakePaymentGateway
) -> None:
    result = container.subscribe_to_plan.execute(
        SubscribeCommand(customer_id="cus-1", plan_id="pro", trial_days=14)
    )
    clock.set(datetime(2026, 1, 15, tzinfo=UTC))

    report = container.renew_due_subscriptions.execute()

    assert report.invoiced == 1
    view = container.queries.get_subscription(result.subscription.id)
    assert view.status == "active"
    assert gateway.settled_amount == 3_000


def test_failed_renewal_moves_to_past_due_then_cancels_after_the_grace_period(
    uow_factory: UnitOfWorkFactory, clock: FixedClock, ids: SequentialIdGenerator
) -> None:
    """支払い失敗 → 猶予 14 日 → 自動解約、までを時計だけで再現する。"""
    gateway = FakePaymentGateway(decline_when=lambda attempt: attempt.description.endswith("sub"))
    container = Container(uow_factory=uow_factory, clock=clock, ids=ids, gateway=gateway)
    container.seed_plans()
    subscription_id = subscribe(container)

    # 2 月の更新で決済が失敗するようにする。
    gateway._decline_when = lambda _: True  # noqa: SLF001
    clock.set(FEB)
    report = container.renew_due_subscriptions.execute()

    assert report.payment_failed == 1
    assert container.queries.get_subscription(subscription_id).status == "past_due"

    # 猶予期間の途中では解約されない。
    clock.set(datetime(2026, 2, 10, tzinfo=UTC))
    assert container.renew_due_subscriptions.execute().canceled_for_nonpayment == 0
    assert container.queries.get_subscription(subscription_id).status == "past_due"

    # 14 日を過ぎると解約される。
    clock.set(datetime(2026, 2, 16, tzinfo=UTC))
    assert container.renew_due_subscriptions.execute().canceled_for_nonpayment == 1
    assert container.queries.get_subscription(subscription_id).status == "canceled"


def test_the_batch_processes_each_subscription_independently(
    container: Container, clock: FixedClock
) -> None:
    """複数件あっても、契約ごとにトランザクションが分かれている。"""
    ids_created = [subscribe(container, customer=f"cus-{index}") for index in range(3)]
    clock.set(FEB)

    report = container.renew_due_subscriptions.execute()

    assert report.renewed == 3
    for subscription_id in ids_created:
        view = container.queries.get_subscription(SubscriptionId(subscription_id))
        assert view.current_period_start == FEB

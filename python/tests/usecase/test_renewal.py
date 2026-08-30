"""更新バッチのテスト。

時計を進めるだけで「1 か月後」「猶予期間の 14 日後」を再現できる。実時間に依存する
テストは書かない。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
    gateway = FakePaymentGateway()
    container = Container(uow_factory=uow_factory, clock=clock, ids=ids, gateway=gateway)
    container.seed_plans()
    subscription_id = subscribe(container)

    # 2 月の更新から決済が拒否されるようにする。
    gateway.set_decline(lambda _: True)
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


def test_every_past_due_subscription_is_expired_independently(
    uow_factory: UnitOfWorkFactory, clock: FixedClock, ids: SequentialIdGenerator
) -> None:
    """猶予切れの契約が複数あっても、1 件ずつ独立して解約される。

    まとめて 1 トランザクションにしていると、DynamoDB では 100 件で頭打ちになる。
    ユースケースの limit が永続化実装の制約と結びついてしまうため、
    バッチの粒度を 1 件に寄せてある。
    """
    declining = FakePaymentGateway(decline_when=lambda _: True)
    container = Container(uow_factory=uow_factory, clock=clock, ids=ids, gateway=declining)
    container.seed_plans()

    subscription_ids = [
        container.subscribe_to_plan.execute(
            SubscribeCommand(customer_id=f"cus-{index}", plan_id="basic")
        ).subscription.id
        for index in range(3)
    ]
    for subscription_id in subscription_ids:
        assert container.queries.get_subscription(subscription_id).status == "past_due"

    # 猶予 14 日を過ぎる。契約期間はまだ満了していないので、更新は走らない。
    clock.set(datetime(2026, 1, 16, tzinfo=UTC))
    report = container.renew_due_subscriptions.execute()

    assert report.renewed == 0
    assert report.canceled_for_nonpayment == 3
    for subscription_id in subscription_ids:
        assert container.queries.get_subscription(subscription_id).status == "canceled"


def test_a_gateway_outage_does_not_stop_the_batch(
    uow_factory: UnitOfWorkFactory, clock: FixedClock, ids: SequentialIdGenerator
) -> None:
    """1 件の決済が通信失敗しても、残りの契約は処理される。

    ここで例外が上まで抜けると被害が二重になる。この契約より後ろが処理されず、
    しかも更新自体は commit 済みなので次回の実行では is_due が偽になり、
    発行済みの請求書を誰も拾わなくなる。契約は active のままなのでサービスは
    提供され続け、静かに売上が消える。
    """
    gateway = FakePaymentGateway()
    container = Container(uow_factory=uow_factory, clock=clock, ids=ids, gateway=gateway)
    container.seed_plans()
    for index in range(3):
        subscribe(container, customer=f"cus-{index}")

    # 更新のタイミングで、1 件だけ決済代行に届かなくする。
    gateway.set_fail(lambda attempt: attempt.customer_id == "cus-0")
    clock.set(FEB)

    report = container.renew_due_subscriptions.execute()

    assert report.renewed == 3
    assert report.invoiced == 3
    assert report.charge_unreachable == 1
    # 「届かなかった」を「拒否された」と混ぜない。混ぜると通信障害で顧客が解約される。
    assert report.payment_failed == 0


def test_unsettled_invoices_are_settled_later(
    uow_factory: UnitOfWorkFactory, clock: FixedClock, ids: SequentialIdGenerator
) -> None:
    """通信失敗で取り残された請求書を、あとから拾い直せる。

    決済 API の呼び出しをトランザクションの外に出している以上、結果を反映する前に
    落ちる窓は必ず開く。この後始末が存在して初めて、その設計が成立する。
    """
    gateway = FakePaymentGateway()
    container = Container(uow_factory=uow_factory, clock=clock, ids=ids, gateway=gateway)
    container.seed_plans()
    subscription_id = subscribe(container)

    gateway.set_fail(lambda _: True)
    clock.set(FEB)
    assert container.renew_due_subscriptions.execute().charge_unreachable == 1
    assert [invoice.status for invoice in container.queries.list_invoices("cus-1")] == [
        "paid",
        "open",
    ]

    # 決済代行が復旧した。
    gateway.set_fail(None)
    clock.set(FEB + timedelta(hours=1))
    report = container.settle_unpaid_invoices.execute()

    assert report.examined == 1
    assert report.settled == 1
    assert [invoice.status for invoice in container.queries.list_invoices("cus-1")] == [
        "paid",
        "paid",
    ]
    assert container.queries.get_subscription(subscription_id).status == "active"
    assert gateway.settled_amount == 2_000


def test_settlement_ignores_invoices_that_may_still_be_in_flight(
    uow_factory: UnitOfWorkFactory, clock: FixedClock, ids: SequentialIdGenerator
) -> None:
    """発行直後の請求書は掴まない。いま決済中かもしれない。"""
    gateway = FakePaymentGateway()
    container = Container(uow_factory=uow_factory, clock=clock, ids=ids, gateway=gateway)
    container.seed_plans()
    subscribe(container)

    gateway.set_fail(lambda _: True)
    clock.set(FEB)
    container.renew_due_subscriptions.execute()

    gateway.set_fail(None)
    # まだ 15 分経っていない。
    clock.set(FEB + timedelta(minutes=5))
    assert container.settle_unpaid_invoices.execute().examined == 0

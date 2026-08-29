"""Subscription 集約の状態遷移テスト。

「支払いに失敗してから 14 日で解約される」を、14 日待たずに検証できる。時刻を
引数で受け取る設計にしてある効果がそのまま出る場所。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from billing.domain.errors import IllegalTransition, InvariantViolation
from billing.domain.ids import CustomerId, PlanId, SubscriptionId
from billing.domain.money import Money
from billing.domain.plan import BillingInterval, Plan
from billing.domain.subscription import GRACE_PERIOD, Subscription, SubscriptionStatus

JAN = datetime(2026, 1, 1, tzinfo=UTC)
FEB = datetime(2026, 2, 1, tzinfo=UTC)

BASIC = Plan(PlanId("basic"), "Basic", Money(1_000), BillingInterval.MONTHLY)
PRO = Plan(PlanId("pro"), "Pro", Money(3_000), BillingInterval.MONTHLY)


def make(plan: Plan = BASIC, *, at: datetime = JAN, trial: timedelta | None = None) -> Subscription:
    return Subscription.subscribe(
        id=SubscriptionId("sub-1"),
        customer_id=CustomerId("cus-1"),
        plan=plan,
        at=at,
        trial=trial,
    )


def test_subscribing_without_trial_starts_active_for_one_interval() -> None:
    subscription = make()

    assert subscription.status is SubscriptionStatus.ACTIVE
    assert subscription.current_period.start == JAN
    assert subscription.current_period.end == FEB
    assert subscription.trial_end is None


def test_subscribing_with_trial_starts_trialing() -> None:
    subscription = make(trial=timedelta(days=14))

    assert subscription.status is SubscriptionStatus.TRIALING
    assert subscription.current_period.end == JAN + timedelta(days=14)
    assert subscription.trial_end == JAN + timedelta(days=14)


def test_changing_plan_during_trial_costs_nothing() -> None:
    """試用中はまだ請求していないので、返すものも取るものもない。"""
    subscription = make(trial=timedelta(days=14))

    proration = subscription.change_plan(
        current_plan=BASIC, new_plan=PRO, at=JAN + timedelta(days=3)
    )

    assert proration.is_noop
    assert subscription.plan_id == PRO.id


def test_changing_plan_mid_period_prorates() -> None:
    subscription = make()

    proration = subscription.change_plan(
        current_plan=BASIC, new_plan=PRO, at=datetime(2026, 1, 16, 12, tzinfo=UTC)
    )

    assert proration.net == Money(1_000)
    assert subscription.plan_id == PRO.id


def test_changing_to_the_same_plan_is_rejected() -> None:
    subscription = make()
    with pytest.raises(InvariantViolation):
        subscription.change_plan(current_plan=BASIC, new_plan=BASIC, at=JAN)


def test_changing_plan_requires_the_actual_current_plan() -> None:
    """呼び出し側が別の契約のプランを渡してきたら落とす。"""
    subscription = make()
    with pytest.raises(InvariantViolation):
        subscription.change_plan(current_plan=PRO, new_plan=BASIC, at=JAN)


def test_cancel_at_period_end_keeps_the_subscription_usable() -> None:
    subscription = make()

    subscription.cancel(at=datetime(2026, 1, 10, tzinfo=UTC))

    assert subscription.status is SubscriptionStatus.ACTIVE
    assert subscription.cancel_at_period_end is True


def test_scheduled_cancellation_takes_effect_at_renewal() -> None:
    subscription = make()
    subscription.cancel(at=datetime(2026, 1, 10, tzinfo=UTC))

    needs_charge = subscription.renew(plan=BASIC, at=FEB)

    assert needs_charge is False
    assert subscription.status is SubscriptionStatus.CANCELED
    assert subscription.canceled_at == FEB


def test_immediate_cancellation_terminates_now() -> None:
    subscription = make()

    subscription.cancel(at=datetime(2026, 1, 10, tzinfo=UTC), immediately=True)

    assert subscription.status is SubscriptionStatus.CANCELED
    assert subscription.canceled_at == datetime(2026, 1, 10, tzinfo=UTC)


def test_canceled_subscription_rejects_everything() -> None:
    subscription = make()
    subscription.cancel(at=JAN, immediately=True)

    with pytest.raises(IllegalTransition):
        subscription.cancel(at=FEB)
    with pytest.raises(IllegalTransition):
        subscription.change_plan(current_plan=BASIC, new_plan=PRO, at=FEB)
    with pytest.raises(IllegalTransition):
        subscription.renew(plan=BASIC, at=FEB)


def test_renewal_starts_from_the_previous_period_end_not_from_now() -> None:
    """バッチが遅れて動いても請求日がずれない。"""
    subscription = make()

    subscription.renew(plan=BASIC, at=FEB + timedelta(hours=5))

    assert subscription.current_period.start == FEB
    assert subscription.current_period.end == datetime(2026, 3, 1, tzinfo=UTC)


def test_renewal_before_the_period_ends_is_rejected() -> None:
    subscription = make()
    with pytest.raises(InvariantViolation):
        subscription.renew(plan=BASIC, at=datetime(2026, 1, 20, tzinfo=UTC))


def test_trial_ends_by_renewing_into_an_active_period() -> None:
    subscription = make(trial=timedelta(days=14))
    trial_end = JAN + timedelta(days=14)

    needs_charge = subscription.renew(plan=BASIC, at=trial_end)

    assert needs_charge is True
    assert subscription.status is SubscriptionStatus.ACTIVE
    assert subscription.current_period.start == trial_end


def test_payment_failure_moves_to_past_due() -> None:
    subscription = make()

    subscription.mark_payment_failed(at=FEB)

    assert subscription.status is SubscriptionStatus.PAST_DUE
    assert subscription.past_due_since == FEB


def test_repeated_failures_do_not_extend_the_grace_period() -> None:
    """再試行のたびに猶予が延びると、永遠に解約されない契約ができる。"""
    subscription = make()
    subscription.mark_payment_failed(at=FEB)

    subscription.mark_payment_failed(at=FEB + timedelta(days=10))

    assert subscription.past_due_since == FEB


def test_successful_payment_recovers_from_past_due() -> None:
    subscription = make()
    subscription.mark_payment_failed(at=FEB)

    subscription.mark_payment_succeeded(at=FEB + timedelta(days=1))

    assert subscription.status is SubscriptionStatus.ACTIVE
    assert subscription.past_due_since is None


def test_grace_period_boundary() -> None:
    subscription = make()
    subscription.mark_payment_failed(at=FEB)

    assert subscription.expire_if_grace_over(at=FEB + GRACE_PERIOD - timedelta(seconds=1)) is False
    assert subscription.status is SubscriptionStatus.PAST_DUE

    assert subscription.expire_if_grace_over(at=FEB + GRACE_PERIOD) is True
    # 一度変数に受け直す。直前の assert で mypy が型を PAST_DUE に絞り込んでおり、
    # そのままだと「ありえない比較」として静的に弾かれてしまう。
    final_status: SubscriptionStatus = subscription.status
    assert final_status is SubscriptionStatus.CANCELED


def test_events_are_recorded_and_drained() -> None:
    subscription = make()
    subscription.cancel(at=JAN, immediately=True)

    names = [event.name for event in subscription.pull_events()]

    assert names == ["subscription.created", "subscription.canceled"]
    assert subscription.pull_events() == []

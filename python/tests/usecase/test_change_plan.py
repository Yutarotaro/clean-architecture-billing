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

    descriptions = [description for description, _ in result.invoice.lines]
    amounts = [amount.amount for _, amount in result.invoice.lines]
    assert descriptions == ["Basic 未使用分", "Pro 残期間分"]
    assert amounts == [-500, 1_500]


def test_downgrading_does_not_charge(
    container: Container, clock: FixedClock, gateway: FakePaymentGateway
) -> None:
    """差額が負なら決済しない。新しい料金は次の期間から効く。

    請求書自体は記録として残し、決済せずに決着させる。作らないでいると
    冪等キーを記録する場所がなくなる（下のテストを参照）。
    """
    subscription_id = subscribe(container, "pro")
    clock.set(MID_JANUARY)

    result = container.change_plan.execute(
        ChangePlanCommand(
            subscription_id=subscription_id, new_plan_id="basic", idempotency_key="change-1"
        )
    )

    assert result.proration.net.amount == -1_000
    assert result.invoice.status == "no_payment_due"
    assert result.invoice.total.amount == -1_000
    assert gateway.settled_amount == 3_000  # 初回請求のぶんだけ


def test_replaying_a_downgrade_is_idempotent(
    container: Container, clock: FixedClock, gateway: FakePaymentGateway
) -> None:
    """差額が 0 以下のプラン変更も、再送で冪等であること。

    冪等キーは請求書にしか記録されない。差額が負のときに請求書を作らない実装だと
    再送を検知する手がかりが残らず、2 回目が「すでにそのプランです」という
    422 になる。タイムアウトして再送しただけのクライアントに、成功した操作が
    エラーとして返ることになる。
    """
    subscription_id = subscribe(container, "pro")
    clock.set(MID_JANUARY)
    command = ChangePlanCommand(
        subscription_id=subscription_id, new_plan_id="basic", idempotency_key="change-1"
    )

    first = container.change_plan.execute(command)
    second = container.change_plan.execute(command)

    assert first.invoice.id == second.invoice.id
    assert second.proration.net.amount == -1_000
    assert second.subscription.plan_id == "basic"
    assert gateway.settled_amount == 3_000


def test_replaying_a_trial_plan_change_is_idempotent(container: Container) -> None:
    """試用中の変更は差額ゼロなので、ダウングレードと同じ穴があった。"""
    subscribed = container.subscribe_to_plan.execute(
        SubscribeCommand(customer_id="cus-1", plan_id="basic", trial_days=14)
    )
    command = ChangePlanCommand(
        subscription_id=subscribed.subscription.id,
        new_plan_id="pro",
        idempotency_key="change-1",
    )

    first = container.change_plan.execute(command)
    second = container.change_plan.execute(command)

    assert first.invoice.id == second.invoice.id
    assert second.proration.net.amount == 0
    assert second.subscription.plan_id == "pro"


def test_a_client_key_cannot_collide_with_an_internal_key(
    container: Container, clock: FixedClock
) -> None:
    """クライアントが内部で使う鍵と同じ文字列を送ってきても壊れない。

    ``initial:<id>`` は初回請求に使う鍵。名前空間を分けていないと、その請求書が
    引き当てられ、明細 1 行の請求書を明細 2 行として復元しようとして
    IndexError になり 500 を返す。Idempotency-Key は任意の文字列を取れるので、
    これはクライアントから引き起こせる。
    """
    subscription_id = subscribe(container)
    clock.set(MID_JANUARY)

    result = container.change_plan.execute(
        ChangePlanCommand(
            subscription_id=subscription_id,
            new_plan_id="pro",
            idempotency_key=f"initial:{subscription_id}",
        )
    )

    assert result.proration.net.amount == 1_000
    assert result.invoice.status == "paid"


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

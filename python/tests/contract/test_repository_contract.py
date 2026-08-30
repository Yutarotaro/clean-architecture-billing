"""リポジトリの契約テスト。

「インターフェースが同じ」だけでは意味がなく、「同じように振る舞う」ことが必要である。
このファイルの全テストが memory / SQLite / DynamoDB の 3 実装で通ることが、
ユースケース層が永続化技術から本当に独立していることの根拠になる。

新しい永続化実装を足したくなったら、まずこのファイルを緑にすればよい。
逆に、ここに書けない振る舞い（実装ごとに違ってしまうもの）を見つけたら、それは
抽象が漏れている箇所であり、docs/persistence-portability.md に記録してある。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from billing.application.charging import UnitOfWorkFactory
from billing.application.errors import ConcurrencyConflict
from billing.domain.ids import CustomerId, InvoiceId, PlanId, SubscriptionId
from billing.domain.invoice import Invoice, InvoiceLine
from billing.domain.money import Money
from billing.domain.plan import BillingInterval, Plan
from billing.domain.subscription import Subscription, SubscriptionStatus
from billing.infrastructure.persistence.errors import DuplicateEntity

JAN = datetime(2026, 1, 1, tzinfo=UTC)
FEB = datetime(2026, 2, 1, tzinfo=UTC)

BASIC = Plan(PlanId("basic"), "Basic", Money(1_000), BillingInterval.MONTHLY)
PRO = Plan(PlanId("pro"), "Pro", Money(3_000), BillingInterval.MONTHLY)


def make_subscription(suffix: str = "1", *, at: datetime = JAN) -> Subscription:
    return Subscription.subscribe(
        id=SubscriptionId(f"sub-{suffix}"),
        customer_id=CustomerId("cus-1"),
        plan=BASIC,
        at=at,
    )


def make_invoice(suffix: str = "1", *, key: str | None = None) -> Invoice:
    return Invoice.issue(
        id=InvoiceId(f"inv-{suffix}"),
        customer_id=CustomerId("cus-1"),
        subscription_id=SubscriptionId("sub-1"),
        lines=[InvoiceLine("Basic", Money(1_000))],
        currency="JPY",
        at=JAN,
        idempotency_key=key,
    )


@pytest.fixture
def seeded(uow_factory: UnitOfWorkFactory) -> UnitOfWorkFactory:
    with uow_factory() as uow:
        uow.plans.add(BASIC)
        uow.plans.add(PRO)
        uow.commit()
    return uow_factory


# --------------------------------------------------------------------- プラン


def test_plans_round_trip(seeded: UnitOfWorkFactory) -> None:
    with seeded() as uow:
        assert uow.plans.get(PlanId("basic")) == BASIC
        assert uow.plans.get(PlanId("missing")) is None
        assert [plan.id for plan in uow.plans.list_all()] == ["basic", "pro"]


# ----------------------------------------------------------------------- 契約


def test_subscription_round_trip(seeded: UnitOfWorkFactory) -> None:
    """保存して読み直したものが、元と同じ値であること。

    値オブジェクト（BillingPeriod、Money）が列や属性に分解され、また組み立て直される
    経路が正しいかを見ている。tz が落ちていればここで落ちる。
    """
    original = make_subscription()
    with seeded() as uow:
        uow.subscriptions.add(original)
        uow.commit()

    with seeded() as uow:
        loaded = uow.subscriptions.get(SubscriptionId("sub-1"))

    assert loaded == original
    assert loaded is not None
    assert loaded.current_period.start.tzinfo is not None


def test_missing_subscription_is_none(seeded: UnitOfWorkFactory) -> None:
    with seeded() as uow:
        assert uow.subscriptions.get(SubscriptionId("nope")) is None


def test_changes_are_invisible_until_commit(seeded: UnitOfWorkFactory) -> None:
    """commit しなければ何も残らない。"""
    with seeded() as uow:
        uow.subscriptions.add(make_subscription())
        # commit を呼ばずに抜ける

    with seeded() as uow:
        assert uow.subscriptions.get(SubscriptionId("sub-1")) is None


def test_explicit_rollback_discards_changes(seeded: UnitOfWorkFactory) -> None:
    with seeded() as uow:
        uow.subscriptions.add(make_subscription())
        uow.rollback()
        uow.commit()

    with seeded() as uow:
        assert uow.subscriptions.get(SubscriptionId("sub-1")) is None


def test_saving_persists_state_transitions(seeded: UnitOfWorkFactory) -> None:
    with seeded() as uow:
        uow.subscriptions.add(make_subscription())
        uow.commit()

    with seeded() as uow:
        subscription = uow.subscriptions.get(SubscriptionId("sub-1"))
        assert subscription is not None
        subscription.cancel(at=JAN, immediately=True)
        uow.subscriptions.save(subscription)
        uow.commit()

    with seeded() as uow:
        reloaded = uow.subscriptions.get(SubscriptionId("sub-1"))
        assert reloaded is not None
        assert reloaded.status is SubscriptionStatus.CANCELED
        assert reloaded.canceled_at == JAN


def test_adding_the_same_id_twice_is_rejected(seeded: UnitOfWorkFactory) -> None:
    with seeded() as uow:
        uow.subscriptions.add(make_subscription())
        uow.commit()

    # 重複は add の時点か commit の時点のどちらかで検出される。どちらになるかは
    # 実装によって違うため、契約としては「この区間のどこかで送出される」と定める。
    with pytest.raises(DuplicateEntity), seeded() as uow:
        uow.subscriptions.add(make_subscription())
        uow.commit()


# ------------------------------------------------------------------ 一覧の絞り込み


def test_list_due_returns_only_expired_periods(seeded: UnitOfWorkFactory) -> None:
    with seeded() as uow:
        uow.subscriptions.add(make_subscription("1", at=JAN))
        uow.subscriptions.add(make_subscription("2", at=FEB))
        uow.commit()

    with seeded() as uow:
        due = uow.subscriptions.list_due(FEB)

    assert [s.id for s in due] == ["sub-1"]


def test_list_due_excludes_canceled_subscriptions(seeded: UnitOfWorkFactory) -> None:
    """解約済みは二度と更新対象にならない。"""
    with seeded() as uow:
        uow.subscriptions.add(make_subscription())
        uow.commit()

    with seeded() as uow:
        stored = uow.subscriptions.get(SubscriptionId("sub-1"))
        assert stored is not None
        stored.cancel(at=JAN, immediately=True)
        uow.subscriptions.save(stored)
        uow.commit()

    with seeded() as uow:
        assert uow.subscriptions.list_due(FEB) == []


def test_list_due_respects_the_limit(seeded: UnitOfWorkFactory) -> None:
    with seeded() as uow:
        for index in range(5):
            uow.subscriptions.add(make_subscription(str(index)))
        uow.commit()

    with seeded() as uow:
        assert len(uow.subscriptions.list_due(FEB, limit=3)) == 3


def test_list_past_due_finds_only_past_due(seeded: UnitOfWorkFactory) -> None:
    with seeded() as uow:
        uow.subscriptions.add(make_subscription("1"))
        uow.subscriptions.add(make_subscription("2"))
        uow.commit()

    with seeded() as uow:
        subscription = uow.subscriptions.get(SubscriptionId("sub-2"))
        assert subscription is not None
        subscription.mark_payment_failed(at=FEB)
        uow.subscriptions.save(subscription)
        uow.commit()

    with seeded() as uow:
        assert [s.id for s in uow.subscriptions.list_past_due()] == ["sub-2"]


# --------------------------------------------------------------------- 請求書


def test_invoice_round_trip_keeps_lines_in_order(seeded: UnitOfWorkFactory) -> None:
    invoice = Invoice.issue(
        id=InvoiceId("inv-1"),
        customer_id=CustomerId("cus-1"),
        subscription_id=SubscriptionId("sub-1"),
        lines=[
            InvoiceLine("Basic 未使用分", Money(-500)),
            InvoiceLine("Pro 残期間分", Money(1_500)),
        ],
        currency="JPY",
        at=JAN,
    )
    with seeded() as uow:
        uow.subscriptions.add(make_subscription())
        uow.invoices.add(invoice)
        uow.commit()

    with seeded() as uow:
        loaded = uow.invoices.get(InvoiceId("inv-1"))

    assert loaded == invoice
    assert loaded is not None
    assert [line.description for line in loaded.lines] == ["Basic 未使用分", "Pro 残期間分"]
    assert loaded.total == Money(1_000)


def test_idempotency_key_lookup(seeded: UnitOfWorkFactory) -> None:
    with seeded() as uow:
        uow.subscriptions.add(make_subscription())
        uow.invoices.add(make_invoice(key="renew:sub-1:2026-02-01"))
        uow.commit()

    with seeded() as uow:
        found = uow.invoices.find_by_idempotency_key("renew:sub-1:2026-02-01")
        assert found is not None
        assert found.id == "inv-1"
        assert uow.invoices.find_by_idempotency_key("other") is None


def test_idempotency_key_is_unique(seeded: UnitOfWorkFactory) -> None:
    """同じ鍵で 2 通目は作れない。二重請求を防ぐ最後の砦。"""
    with seeded() as uow:
        uow.subscriptions.add(make_subscription())
        uow.invoices.add(make_invoice("1", key="dup"))
        uow.commit()

    with pytest.raises(DuplicateEntity), seeded() as uow:
        uow.invoices.add(make_invoice("2", key="dup"))
        uow.commit()


def test_list_invoices_for_customer(seeded: UnitOfWorkFactory) -> None:
    with seeded() as uow:
        uow.subscriptions.add(make_subscription())
        uow.invoices.add(make_invoice("1", key="a"))
        uow.invoices.add(make_invoice("2", key="b"))
        uow.commit()

    with seeded() as uow:
        found = uow.invoices.list_for_customer(CustomerId("cus-1"))
        assert {invoice.id for invoice in found} == {"inv-1", "inv-2"}
        assert uow.invoices.list_for_customer(CustomerId("cus-999")) == []


# ------------------------------------------------------------------- 楽観ロック


def test_concurrent_update_is_detected(seeded: UnitOfWorkFactory) -> None:
    """先に読んだ側が後から書くと弾かれる（lost update の防止）。

    衝突が ``save`` で分かるか ``commit`` で分かるかは実装によって異なる。
    SQL は UPDATE の影響行数で即座に、DynamoDB は TransactWriteItems を送るまで
    分からない。呼び出し側は両方を囲んで捕まえる必要がある、というのが契約である。
    """
    with seeded() as uow:
        uow.subscriptions.add(make_subscription())
        uow.commit()

    with seeded() as outer:
        stale = outer.subscriptions.get(SubscriptionId("sub-1"))
        assert stale is not None

        with seeded() as inner:
            fresh = inner.subscriptions.get(SubscriptionId("sub-1"))
            assert fresh is not None
            fresh.cancel(at=JAN, immediately=True)
            inner.subscriptions.save(fresh)
            inner.commit()

        stale.cancel(at=JAN + timedelta(days=1))
        with pytest.raises(ConcurrencyConflict):
            outer.subscriptions.save(stale)
            outer.commit()


def test_saving_without_reading_is_rejected(seeded: UnitOfWorkFactory) -> None:
    """読まずに save するのは、誰かの変更を無条件に上書きすることに等しい。"""
    with seeded() as uow:
        uow.subscriptions.add(make_subscription())
        uow.commit()

    with pytest.raises(ConcurrencyConflict), seeded() as uow:
        uow.subscriptions.save(make_subscription())
        uow.commit()


# --------------------------------------------------------- 決着していない請求書


def _seed_subscription(uow_factory: UnitOfWorkFactory) -> None:
    with uow_factory() as uow:
        uow.subscriptions.add(make_subscription())
        uow.commit()


def test_list_unsettled_returns_only_open_invoices(seeded: UnitOfWorkFactory) -> None:
    """決着していない請求書だけを返す。

    決済 API の呼び出しはトランザクションの外で行うため、結果を反映する前に
    プロセスが落ちると請求書が open のまま残る。これはそれを拾い直すための入口。
    """
    _seed_subscription(seeded)
    with seeded() as uow:
        uow.invoices.add(make_invoice("1", key="a"))
        paid = make_invoice("2", key="b")
        paid.mark_paid(at=JAN)
        uow.invoices.add(paid)
        uow.commit()

    with seeded() as uow:
        found = uow.invoices.list_unsettled(issued_before=FEB)

    assert [invoice.id for invoice in found] == ["inv-1"]


def test_list_unsettled_excludes_recently_issued_invoices(seeded: UnitOfWorkFactory) -> None:
    """発行直後のものは掴まない。いま決済中かもしれない。"""
    _seed_subscription(seeded)
    with seeded() as uow:
        uow.invoices.add(make_invoice("1", key="a"))
        uow.commit()

    with seeded() as uow:
        found = uow.invoices.list_unsettled(issued_before=JAN - timedelta(seconds=1))

    assert found == []


def test_list_unsettled_respects_the_limit(seeded: UnitOfWorkFactory) -> None:
    _seed_subscription(seeded)
    with seeded() as uow:
        for index in range(5):
            uow.invoices.add(make_invoice(str(index), key=f"key-{index}"))
        uow.commit()

    with seeded() as uow:
        assert len(uow.invoices.list_unsettled(issued_before=FEB, limit=3)) == 3


def test_settled_invoices_leave_the_unsettled_list(seeded: UnitOfWorkFactory) -> None:
    """決着させたら次からは拾われない。"""
    _seed_subscription(seeded)
    with seeded() as uow:
        uow.invoices.add(make_invoice("1", key="a"))
        uow.commit()

    with seeded() as uow:
        invoice = uow.invoices.get(InvoiceId("inv-1"))
        assert invoice is not None
        invoice.mark_paid(at=JAN)
        uow.invoices.save(invoice)
        uow.commit()

    with seeded() as uow:
        assert uow.invoices.list_unsettled(issued_before=FEB) == []

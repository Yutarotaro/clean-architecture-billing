"""合成ルート（composition root）。

依存性注入のコンテナライブラリは使っていない。使うほどの規模ではないというのも
あるが、それ以上に「何がどこに注入されているか」がこのファイルを読むだけで分かる
状態を保ちたいため。DI コンテナは配線を隠す道具であって、なくす道具ではない。

依存の向きがこの 1 ファイルに集中している点に注目してほしい。ユースケースは
具体的な実装クラスの名前を 1 つも知らず、それを知っているのはここだけである。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from billing.application.charging import UnitOfWorkFactory
from billing.application.ports import Clock, IdGenerator, PaymentGateway
from billing.application.queries import BillingQueries
from billing.application.usecases import (
    CancelSubscription,
    ChangePlan,
    RecordPaymentResult,
    RenewDueSubscriptions,
    SettleUnpaidInvoices,
    SubscribeToPlan,
)
from billing.domain.ids import PlanId
from billing.domain.money import Money
from billing.domain.plan import BillingInterval, Plan
from billing.infrastructure.clock import SystemClock
from billing.infrastructure.ids import UuidGenerator
from billing.infrastructure.payment.fake_gateway import FakePaymentGateway
from billing.presentation.settings import PersistenceKind, Settings

#: サンプルを起動してすぐ触れるようにするための初期データ。
DEFAULT_PLANS = (
    Plan(PlanId("basic"), "Basic", Money(1_000), BillingInterval.MONTHLY),
    Plan(PlanId("pro"), "Pro", Money(3_000), BillingInterval.MONTHLY),
    Plan(PlanId("pro-yearly"), "Pro (yearly)", Money(30_000), BillingInterval.YEARLY),
)


@dataclass
class Container:
    """アプリケーション全体の配線。"""

    uow_factory: UnitOfWorkFactory
    clock: Clock
    ids: IdGenerator
    gateway: PaymentGateway

    @cached_property
    def subscribe_to_plan(self) -> SubscribeToPlan:
        return SubscribeToPlan(
            uow_factory=self.uow_factory,
            clock=self.clock,
            ids=self.ids,
            gateway=self.gateway,
        )

    @cached_property
    def change_plan(self) -> ChangePlan:
        return ChangePlan(
            uow_factory=self.uow_factory,
            clock=self.clock,
            ids=self.ids,
            gateway=self.gateway,
        )

    @cached_property
    def cancel_subscription(self) -> CancelSubscription:
        return CancelSubscription(uow_factory=self.uow_factory, clock=self.clock)

    @cached_property
    def record_payment_result(self) -> RecordPaymentResult:
        return RecordPaymentResult(uow_factory=self.uow_factory, clock=self.clock)

    @cached_property
    def renew_due_subscriptions(self) -> RenewDueSubscriptions:
        return RenewDueSubscriptions(
            uow_factory=self.uow_factory,
            clock=self.clock,
            ids=self.ids,
            gateway=self.gateway,
        )

    @cached_property
    def settle_unpaid_invoices(self) -> SettleUnpaidInvoices:
        return SettleUnpaidInvoices(
            uow_factory=self.uow_factory, clock=self.clock, gateway=self.gateway
        )

    @cached_property
    def queries(self) -> BillingQueries:
        return BillingQueries(uow_factory=self.uow_factory)

    def seed_plans(self, plans: tuple[Plan, ...] = DEFAULT_PLANS) -> None:
        with self.uow_factory() as uow:
            existing = {plan.id for plan in uow.plans.list_all()}
            for plan in plans:
                if plan.id not in existing:
                    uow.plans.add(plan)
            uow.commit()


def build_container(
    settings: Settings,
    *,
    clock: Clock | None = None,
    ids: IdGenerator | None = None,
    gateway: PaymentGateway | None = None,
) -> Container:
    """設定から実装を選んで組み立てる。

    テストからは ``clock`` や ``gateway`` を差し替えて呼ぶ。本番と同じ配線コードを
    通したうえで、外界だけを置き換えられる。
    """
    container = Container(
        uow_factory=_build_uow_factory(settings),
        clock=clock or SystemClock(),
        ids=ids or UuidGenerator(),
        # 本物の決済代行の adapter はこのサンプルには含まれていない。実装するときは
        # PaymentGateway プロトコルを満たすクラスを infrastructure/payment/ に足し、
        # 差し替えるのはこの 1 行だけになる。
        gateway=gateway or FakePaymentGateway(),
    )
    if settings.seed_plans:
        container.seed_plans()
    return container


def _build_uow_factory(settings: Settings) -> UnitOfWorkFactory:
    match settings.persistence:
        case PersistenceKind.MEMORY:
            from billing.infrastructure.persistence.memory import (
                InMemoryUnitOfWork,
                MemoryDatabase,
            )

            database = MemoryDatabase()
            return lambda: InMemoryUnitOfWork(database)

        case PersistenceKind.SQLITE:
            from billing.infrastructure.persistence.sql import create_schema
            from billing.infrastructure.persistence.sql.uow import (
                SqlAlchemyUnitOfWork,
                create_sqlite_engine,
            )

            engine = create_sqlite_engine(settings.database_url)
            create_schema(engine)
            return lambda: SqlAlchemyUnitOfWork(engine)

        case PersistenceKind.DYNAMODB:
            from billing.infrastructure.persistence.dynamo import (
                DynamoUnitOfWork,
                create_dynamo_client,
            )

            client = create_dynamo_client(
                region_name=settings.aws_region,
                endpoint_url=settings.dynamo_endpoint_url,
            )
            return lambda: DynamoUnitOfWork(client, settings.dynamo_table)

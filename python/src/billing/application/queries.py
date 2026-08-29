"""読み取り専用の問い合わせ。

「一覧を返す」だけの操作にユースケースクラスを作ってもコマンドと同じ重さの器に
なるだけなので、読み取りは別の入口にまとめている。書き込み側（usecases/）が
集約を通して不変条件を守るのに対し、こちらは集約を素通しで View に変換するだけで、
状態を変えない。

規模が大きくなれば、読み取り専用の非正規化ビューを別に持つ（CQRS）方向に伸ばせる。
その場合もユースケース層のコードには手を触れずに済む。
"""

from __future__ import annotations

from billing.application.charging import UnitOfWorkFactory
from billing.application.dto import InvoiceView, PlanView, SubscriptionView
from billing.application.errors import EntityNotFound
from billing.domain.ids import CustomerId, SubscriptionId


class BillingQueries:
    def __init__(self, *, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def list_plans(self) -> list[PlanView]:
        with self._uow_factory() as uow:
            return [PlanView.of(plan) for plan in uow.plans.list_all()]

    def get_subscription(self, subscription_id: str) -> SubscriptionView:
        with self._uow_factory() as uow:
            subscription = uow.subscriptions.get(SubscriptionId(subscription_id))
            if subscription is None:
                raise EntityNotFound("subscription", subscription_id)
            return SubscriptionView.of(subscription)

    def list_invoices(self, customer_id: str) -> list[InvoiceView]:
        with self._uow_factory() as uow:
            return [
                InvoiceView.of(invoice)
                for invoice in uow.invoices.list_for_customer(CustomerId(customer_id))
            ]

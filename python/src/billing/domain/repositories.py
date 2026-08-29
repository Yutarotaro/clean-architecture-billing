"""リポジトリの契約。

インターフェースをドメイン層に置き、実装を infrastructure 層に置く。これが依存性
逆転の実体である。ドメインは「集約を出し入れする何か」を要求するだけで、それが
PostgreSQL なのか dict なのかを知らない。

``Protocol`` を使っているので、実装側はこのモジュールを import しなくてよい。
継承関係が要らないぶん、依存の矢印が本当に一方向になる（ADR-0002 を参照）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from billing.domain.ids import CustomerId, InvoiceId, PlanId, SubscriptionId
from billing.domain.invoice import Invoice
from billing.domain.plan import Plan
from billing.domain.subscription import Subscription


class PlanRepository(Protocol):
    def get(self, plan_id: PlanId) -> Plan | None: ...

    def list_all(self) -> list[Plan]: ...

    def add(self, plan: Plan) -> None: ...


class SubscriptionRepository(Protocol):
    def get(self, subscription_id: SubscriptionId) -> Subscription | None: ...

    def add(self, subscription: Subscription) -> None: ...

    def save(self, subscription: Subscription) -> None: ...

    def list_due(self, at: datetime, *, limit: int = 100) -> list[Subscription]:
        """``at`` 時点で請求期間が満了している契約を返す。

        「全件返して呼び出し側で絞る」にしないのは、契約が 100 万件あっても動く形を
        最初から強制するため。絞り込みの条件はドメインの言葉（期間が満了している）で
        書かれており、SQL の話は実装側に閉じている。
        """
        ...

    def list_past_due(self, *, limit: int = 100) -> list[Subscription]: ...


class InvoiceRepository(Protocol):
    def get(self, invoice_id: InvoiceId) -> Invoice | None: ...

    def add(self, invoice: Invoice) -> None: ...

    def save(self, invoice: Invoice) -> None: ...

    def find_by_idempotency_key(self, key: str) -> Invoice | None: ...

    def list_for_customer(self, customer_id: CustomerId) -> list[Invoice]: ...

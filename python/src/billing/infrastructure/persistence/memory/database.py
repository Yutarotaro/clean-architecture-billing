"""プロセス内に置く「データベース」。"""

from __future__ import annotations

from dataclasses import dataclass, field

from billing.domain.ids import InvoiceId, PlanId, SubscriptionId
from billing.domain.invoice import Invoice
from billing.domain.plan import Plan
from billing.domain.subscription import Subscription


@dataclass
class MemoryDatabase:
    plans: dict[PlanId, Plan] = field(default_factory=dict)
    subscriptions: dict[SubscriptionId, Subscription] = field(default_factory=dict)
    invoices: dict[InvoiceId, Invoice] = field(default_factory=dict)
    #: 楽観ロック用のバージョン。SQL の version 列、DynamoDB の version 属性に相当する。
    versions: dict[str, int] = field(default_factory=dict)

    def replace_with(self, other: MemoryDatabase) -> None:
        self.plans = other.plans
        self.subscriptions = other.subscriptions
        self.invoices = other.invoices
        self.versions = other.versions

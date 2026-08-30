"""dict を使ったリポジトリ実装。

SQL 版・DynamoDB 版と同じ楽観ロックの意味論をここでも実装している。「テスト用だから
競合は起きない」と手を抜くと、契約テスト（tests/contract/）が 3 実装で同じ結果に
ならなくなり、インメモリで通ったテストの意味が消える。
"""

from __future__ import annotations

from datetime import datetime

from billing.domain.ids import CustomerId, InvoiceId, PlanId, SubscriptionId
from billing.domain.invoice import Invoice, InvoiceStatus
from billing.domain.plan import Plan
from billing.domain.subscription import Subscription, SubscriptionStatus
from billing.infrastructure.persistence.errors import DuplicateEntity, UnknownEntity
from billing.infrastructure.persistence.memory.database import MemoryDatabase
from billing.infrastructure.persistence.versioning import VersionTracker


class InMemoryPlanRepository:
    def __init__(self, db: MemoryDatabase) -> None:
        self._db = db

    def get(self, plan_id: PlanId) -> Plan | None:
        return self._db.plans.get(plan_id)

    def list_all(self) -> list[Plan]:
        return sorted(self._db.plans.values(), key=lambda plan: plan.id)

    def add(self, plan: Plan) -> None:
        self._db.plans[plan.id] = plan


class InMemorySubscriptionRepository:
    def __init__(self, db: MemoryDatabase, committed: MemoryDatabase) -> None:
        self._db = db
        # 楽観ロックの判定は、作業コピーではなくコミット済みの実体に対して行う。
        # 作業コピーだけを見ていると、他のトランザクションが先に更新したことに
        # 永遠に気づけない（実際、契約テストがこれを検出した）。
        self._committed = committed
        self._versions = VersionTracker("subscription")

    def get(self, subscription_id: SubscriptionId) -> Subscription | None:
        subscription = self._db.subscriptions.get(subscription_id)
        if subscription is None:
            return None
        self._versions.remember(str(subscription_id), self._db.versions[str(subscription_id)])
        return subscription

    def add(self, subscription: Subscription) -> None:
        entity_id = str(subscription.id)
        if subscription.id in self._db.subscriptions:
            raise DuplicateEntity(f"subscription {entity_id!r} already exists")
        self._db.subscriptions[subscription.id] = subscription
        self._db.versions[entity_id] = 1
        self._versions.remember(entity_id, 1)

    def save(self, subscription: Subscription) -> None:
        entity_id = str(subscription.id)
        if subscription.id not in self._db.subscriptions:
            raise UnknownEntity(f"subscription {entity_id!r} does not exist")
        expected = self._versions.expected(entity_id)
        if self._baseline(entity_id) != expected:
            raise self._versions.conflict(entity_id)
        self._db.subscriptions[subscription.id] = subscription
        self._db.versions[entity_id] = self._versions.bump(entity_id)

    def _baseline(self, entity_id: str) -> int | None:
        """比較の基準にするバージョン。

        コミット済みに存在すればそれを、まだ存在しない（この UnitOfWork で追加した）
        ものは作業コピーのものを使う。
        """
        if entity_id in self._committed.versions:
            return self._committed.versions[entity_id]
        return self._db.versions.get(entity_id)

    def list_due(self, at: datetime, *, limit: int = 100) -> list[Subscription]:
        due = [s for s in self._db.subscriptions.values() if s.is_due(at)]
        due.sort(key=lambda s: s.current_period.end)
        selected = due[:limit]
        for subscription in selected:
            self._versions.remember(str(subscription.id), self._db.versions[str(subscription.id)])
        return selected

    def list_past_due(self, *, limit: int = 100) -> list[Subscription]:
        past_due = [
            s for s in self._db.subscriptions.values() if s.status is SubscriptionStatus.PAST_DUE
        ]
        past_due.sort(key=lambda s: s.past_due_since or s.current_period.start)
        selected = past_due[:limit]
        for subscription in selected:
            self._versions.remember(str(subscription.id), self._db.versions[str(subscription.id)])
        return selected


class InMemoryInvoiceRepository:
    def __init__(self, db: MemoryDatabase, committed: MemoryDatabase) -> None:
        self._db = db
        self._committed = committed
        self._versions = VersionTracker("invoice")

    def get(self, invoice_id: InvoiceId) -> Invoice | None:
        invoice = self._db.invoices.get(invoice_id)
        if invoice is None:
            return None
        self._versions.remember(str(invoice_id), self._db.versions[str(invoice_id)])
        return invoice

    def add(self, invoice: Invoice) -> None:
        entity_id = str(invoice.id)
        if invoice.id in self._db.invoices:
            raise DuplicateEntity(f"invoice {entity_id!r} already exists")
        if invoice.idempotency_key is not None and any(
            other.idempotency_key == invoice.idempotency_key for other in self._db.invoices.values()
        ):
            # SQL の UNIQUE 制約、DynamoDB の条件付き書き込みに相当する検査。
            raise DuplicateEntity(f"idempotency key {invoice.idempotency_key!r} already used")
        self._db.invoices[invoice.id] = invoice
        self._db.versions[entity_id] = 1
        self._versions.remember(entity_id, 1)

    def save(self, invoice: Invoice) -> None:
        entity_id = str(invoice.id)
        if invoice.id not in self._db.invoices:
            raise UnknownEntity(f"invoice {entity_id!r} does not exist")
        expected = self._versions.expected(entity_id)
        if self._baseline(entity_id) != expected:
            raise self._versions.conflict(entity_id)
        self._db.invoices[invoice.id] = invoice
        self._db.versions[entity_id] = self._versions.bump(entity_id)

    def _baseline(self, entity_id: str) -> int | None:
        if entity_id in self._committed.versions:
            return self._committed.versions[entity_id]
        return self._db.versions.get(entity_id)

    def find_by_idempotency_key(self, key: str) -> Invoice | None:
        for invoice in self._db.invoices.values():
            if invoice.idempotency_key == key:
                self._versions.remember(str(invoice.id), self._db.versions[str(invoice.id)])
                return invoice
        return None

    def list_unsettled(self, *, issued_before: datetime, limit: int = 100) -> list[Invoice]:
        found = [
            invoice
            for invoice in self._db.invoices.values()
            if invoice.status is InvoiceStatus.OPEN
            and invoice.issued_at is not None
            and invoice.issued_at <= issued_before
        ]
        found.sort(key=lambda invoice: (invoice.issued_at or datetime.min, invoice.id))
        selected = found[:limit]
        for invoice in selected:
            self._versions.remember(str(invoice.id), self._db.versions[str(invoice.id)])
        return selected

    def list_for_customer(self, customer_id: CustomerId) -> list[Invoice]:
        found = [i for i in self._db.invoices.values() if i.customer_id == customer_id]
        found.sort(key=lambda i: (i.issued_at is None, i.issued_at or datetime.min))
        for invoice in found:
            self._versions.remember(str(invoice.id), self._db.versions[str(invoice.id)])
        return found

"""dict を使ったリポジトリ実装。

SQL 版・DynamoDB 版と同じ楽観ロックの意味論をここでも実装している。「テスト用だから
競合は起きない」と手を抜くと、契約テスト（tests/contract/）が 3 実装で同じ結果に
ならなくなり、インメモリで通ったテストの意味が消える。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from billing.domain.ids import CustomerId, InvoiceId, PlanId, SubscriptionId
from billing.domain.invoice import Invoice, InvoiceStatus
from billing.domain.plan import Plan
from billing.domain.subscription import Subscription, SubscriptionStatus
from billing.infrastructure.persistence.errors import DuplicateEntity, UnknownEntity
from billing.infrastructure.persistence.memory.database import MemoryDatabase, Staging
from billing.infrastructure.persistence.versioning import VersionTracker


class InMemoryPlanRepository:
    def __init__(self, db: MemoryDatabase, staging: Staging) -> None:
        self._db = db
        self._staging = staging

    def get(self, plan_id: PlanId) -> Plan | None:
        if plan_id in self._staging.plans:
            return self._staging.plans[plan_id]
        return self._db.plans.get(plan_id)

    def list_all(self) -> list[Plan]:
        merged = {**self._db.plans, **self._staging.plans}
        return sorted(merged.values(), key=lambda plan: plan.id)

    def add(self, plan: Plan) -> None:
        self._staging.plans[plan.id] = plan


class InMemorySubscriptionRepository:
    def __init__(self, db: MemoryDatabase, staging: Staging) -> None:
        self._db = db
        self._staging = staging
        self._versions = VersionTracker("subscription")

    def get(self, subscription_id: SubscriptionId) -> Subscription | None:
        staged = self._staging.subscriptions.get(subscription_id)
        if staged is not None:
            self._versions.remember(
                str(subscription_id), self._staging.versions[str(subscription_id)]
            )
            return deepcopy(staged)
        stored = self._db.subscriptions.get(subscription_id)
        if stored is None:
            return None
        self._versions.remember(str(subscription_id), self._db.versions[str(subscription_id)])
        # 複製して返す。参照をそのまま渡すと、呼び出し側がフィールドを書き換えた瞬間に、
        # commit していないのにデータベースの中身が変わる。
        return deepcopy(stored)

    def add(self, subscription: Subscription) -> None:
        entity_id = str(subscription.id)
        if self._exists(subscription.id):
            raise DuplicateEntity(f"subscription {entity_id!r} already exists")
        self._staging.subscriptions[subscription.id] = deepcopy(subscription)
        self._staging.versions[entity_id] = 1
        self._versions.remember(entity_id, 1)

    def save(self, subscription: Subscription) -> None:
        entity_id = str(subscription.id)
        if not self._exists(subscription.id):
            raise UnknownEntity(f"subscription {entity_id!r} does not exist")
        expected = self._versions.expected(entity_id)
        # 楽観ロックの判定は、staging ではなくコミット済みの実体に対して行う。
        # staging だけを見ていると、他のトランザクションが先に更新したことに気づけない。
        committed = self._db.versions.get(entity_id)
        if committed is not None and committed != expected:
            raise self._versions.conflict(entity_id)
        self._staging.subscriptions[subscription.id] = deepcopy(subscription)
        self._staging.versions[entity_id] = self._versions.bump(entity_id)

    def list_due(self, at: datetime, *, limit: int = 100) -> list[Subscription]:
        found = [s for s in self._merged().values() if s.is_due(at)]
        found.sort(key=lambda s: s.current_period.end)
        return self._take(found, limit)

    def list_past_due(self, *, limit: int = 100) -> list[Subscription]:
        found = [s for s in self._merged().values() if s.status is SubscriptionStatus.PAST_DUE]
        found.sort(key=lambda s: (s.past_due_since or s.current_period.start, s.id))
        return self._take(found, limit)

    def _merged(self) -> dict[SubscriptionId, Subscription]:
        return {**self._db.subscriptions, **self._staging.subscriptions}

    def _take(self, found: list[Subscription], limit: int) -> list[Subscription]:
        selected = found[:limit]
        for subscription in selected:
            entity_id = str(subscription.id)
            version = self._staging.versions.get(entity_id, self._db.versions.get(entity_id))
            if version is not None:
                self._versions.remember(entity_id, version)
        return [deepcopy(subscription) for subscription in selected]

    def _exists(self, subscription_id: SubscriptionId) -> bool:
        return (
            subscription_id in self._staging.subscriptions
            or subscription_id in self._db.subscriptions
        )


class InMemoryInvoiceRepository:
    def __init__(self, db: MemoryDatabase, staging: Staging) -> None:
        self._db = db
        self._staging = staging
        self._versions = VersionTracker("invoice")

    def get(self, invoice_id: InvoiceId) -> Invoice | None:
        staged = self._staging.invoices.get(invoice_id)
        if staged is not None:
            self._versions.remember(str(invoice_id), self._staging.versions[str(invoice_id)])
            return deepcopy(staged)
        stored = self._db.invoices.get(invoice_id)
        if stored is None:
            return None
        self._versions.remember(str(invoice_id), self._db.versions[str(invoice_id)])
        return deepcopy(stored)

    def add(self, invoice: Invoice) -> None:
        entity_id = str(invoice.id)
        if self._exists(invoice.id):
            raise DuplicateEntity(f"invoice {entity_id!r} already exists")
        if invoice.idempotency_key is not None and any(
            other.idempotency_key == invoice.idempotency_key for other in self._merged().values()
        ):
            # SQL の UNIQUE 制約、DynamoDB の条件付き書き込みに相当する検査。
            raise DuplicateEntity(f"idempotency key {invoice.idempotency_key!r} already used")
        self._staging.invoices[invoice.id] = deepcopy(invoice)
        self._staging.versions[entity_id] = 1
        self._versions.remember(entity_id, 1)

    def save(self, invoice: Invoice) -> None:
        entity_id = str(invoice.id)
        if not self._exists(invoice.id):
            raise UnknownEntity(f"invoice {entity_id!r} does not exist")
        expected = self._versions.expected(entity_id)
        committed = self._db.versions.get(entity_id)
        if committed is not None and committed != expected:
            raise self._versions.conflict(entity_id)
        self._staging.invoices[invoice.id] = deepcopy(invoice)
        self._staging.versions[entity_id] = self._versions.bump(entity_id)

    def find_by_idempotency_key(self, key: str) -> Invoice | None:
        for invoice in self._merged().values():
            if invoice.idempotency_key == key:
                self._remember(str(invoice.id))
                return deepcopy(invoice)
        return None

    def list_unsettled(self, *, issued_before: datetime, limit: int = 100) -> list[Invoice]:
        found = [
            invoice
            for invoice in self._merged().values()
            if invoice.status is InvoiceStatus.OPEN
            and invoice.issued_at is not None
            and invoice.issued_at <= issued_before
        ]
        found.sort(key=lambda invoice: (invoice.issued_at or datetime.min, invoice.id))
        return self._take(found, limit)

    def list_for_customer(self, customer_id: CustomerId) -> list[Invoice]:
        found = [i for i in self._merged().values() if i.customer_id == customer_id]
        found.sort(key=lambda i: (i.issued_at is None, i.issued_at or datetime.min, i.id))
        return self._take(found, len(found))

    def _merged(self) -> dict[InvoiceId, Invoice]:
        return {**self._db.invoices, **self._staging.invoices}

    def _take(self, found: list[Invoice], limit: int) -> list[Invoice]:
        selected = found[:limit]
        for invoice in selected:
            self._remember(str(invoice.id))
        return [deepcopy(invoice) for invoice in selected]

    def _remember(self, entity_id: str) -> None:
        version = self._staging.versions.get(entity_id, self._db.versions.get(entity_id))
        if version is not None:
            self._versions.remember(entity_id, version)

    def _exists(self, invoice_id: InvoiceId) -> bool:
        return invoice_id in self._staging.invoices or invoice_id in self._db.invoices

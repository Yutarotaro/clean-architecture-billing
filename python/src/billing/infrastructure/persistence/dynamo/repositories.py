"""DynamoDB によるリポジトリ実装。

インターフェースは SQL 版と完全に同じである。呼び出す側（ユースケース）は、
自分が今 SQL を相手にしているのか DynamoDB を相手にしているのかを知らない。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from billing.domain.ids import CustomerId, InvoiceId, PlanId, SubscriptionId
from billing.domain.invoice import Invoice
from billing.domain.plan import Plan
from billing.domain.subscription import Subscription
from billing.infrastructure.persistence.dynamo import mappers
from billing.infrastructure.persistence.dynamo.schema import (
    GSI1,
    GSI2,
    LIVE_PARTITION,
    PAST_DUE_PARTITION,
    UNSETTLED_PARTITION,
    customer_invoices_partition,
    idempotency_key,
    invoice_key,
    plan_key,
    subscription_key,
)
from billing.infrastructure.persistence.dynamo.session import DynamoSession
from billing.infrastructure.persistence.errors import DuplicateEntity
from billing.infrastructure.persistence.timestamps import require_iso
from billing.infrastructure.persistence.versioning import VersionTracker

#: 新規作成の条件。その主キーの項目がまだ存在しないこと。
_MUST_NOT_EXIST = "attribute_not_exists(pk)"
#: 更新の条件。読んだときからバージョンが動いていないこと。
_VERSION_UNCHANGED = "version = :expected_version"


class DynamoPlanRepository:
    def __init__(self, session: DynamoSession) -> None:
        self._session = session

    def get(self, plan_id: PlanId) -> Plan | None:
        item = self._session.get(plan_key(plan_id))
        return None if item is None else mappers.item_to_plan(item)

    def list_all(self) -> list[Plan]:
        items = self._session.scan(
            filter_expression="#type = :type",
            values={":type": "plan"},
            names={"#type": "type"},
        )
        plans = [mappers.item_to_plan(item) for item in items]
        plans.sort(key=lambda plan: plan.id)
        return plans

    def add(self, plan: Plan) -> None:
        self._session.stage(
            key=plan_key(plan.id),
            item=mappers.plan_to_item(plan, version=1),
            condition=_MUST_NOT_EXIST,
            values={},
            entity="plan",
            entity_id=str(plan.id),
            duplicate_message=f"plan {plan.id!r} already exists",
        )


class DynamoSubscriptionRepository:
    def __init__(self, session: DynamoSession) -> None:
        self._session = session
        self._versions = VersionTracker("subscription")

    def get(self, subscription_id: SubscriptionId) -> Subscription | None:
        item = self._session.get(subscription_key(subscription_id))
        if item is None:
            return None
        return self._track(item)

    def add(self, subscription: Subscription) -> None:
        entity_id = str(subscription.id)
        self._session.stage(
            key=subscription_key(subscription.id),
            item=mappers.subscription_to_item(subscription, version=1),
            condition=_MUST_NOT_EXIST,
            values={},
            entity="subscription",
            entity_id=entity_id,
            duplicate_message=f"subscription {entity_id!r} already exists",
        )
        self._versions.remember(entity_id, 1)

    def save(self, subscription: Subscription) -> None:
        entity_id = str(subscription.id)
        expected = self._versions.expected(entity_id)
        self._session.stage(
            key=subscription_key(subscription.id),
            item=mappers.subscription_to_item(subscription, version=expected + 1),
            condition=_VERSION_UNCHANGED,
            values={":expected_version": expected},
            entity="subscription",
            entity_id=entity_id,
        )
        self._versions.bump(entity_id)

    def list_due(self, at: datetime, *, limit: int = 100) -> list[Subscription]:
        items = self._session.query(
            index=GSI1,
            key_condition="gsi1pk = :live AND gsi1sk <= :now",
            values={":live": LIVE_PARTITION, ":now": require_iso(at, field="at")},
            limit=limit,
        )
        return [self._track(item) for item in items]

    def list_past_due(self, *, limit: int = 100) -> list[Subscription]:
        items = self._session.query(
            index=GSI2,
            key_condition="gsi2pk = :past_due",
            values={":past_due": PAST_DUE_PARTITION},
            limit=limit,
        )
        return [self._track(item) for item in items]

    def _track(self, item: dict[str, Any]) -> Subscription:
        self._versions.remember(item["id"], int(item["version"]))
        return mappers.item_to_subscription(item)


class DynamoInvoiceRepository:
    def __init__(self, session: DynamoSession) -> None:
        self._session = session
        self._versions = VersionTracker("invoice")

    def get(self, invoice_id: InvoiceId) -> Invoice | None:
        item = self._session.get(invoice_key(invoice_id))
        if item is None:
            return None
        return self._track(item)

    def add(self, invoice: Invoice) -> None:
        entity_id = str(invoice.id)
        self._session.stage(
            key=invoice_key(invoice.id),
            item=mappers.invoice_to_item(invoice, version=1),
            condition=_MUST_NOT_EXIST,
            values={},
            entity="invoice",
            entity_id=entity_id,
            duplicate_message=f"invoice {entity_id!r} already exists",
        )
        if invoice.idempotency_key is not None:
            # 冪等キーを「予約済みの印」として別項目に書く。請求書本体と同じ
            # トランザクションに入るので、片方だけが残ることはない。DynamoDB に
            # UNIQUE 制約がなくても、一意性はデータベース側で強制できる。
            key = invoice.idempotency_key
            self._session.stage(
                key=idempotency_key(key),
                item={
                    **idempotency_key(key),
                    "type": "idempotency",
                    "invoice_id": entity_id,
                    "version": 1,
                },
                condition=_MUST_NOT_EXIST,
                values={},
                entity="invoice",
                entity_id=entity_id,
                duplicate_message=f"idempotency key {key!r} already used",
            )
        self._versions.remember(entity_id, 1)

    def save(self, invoice: Invoice) -> None:
        entity_id = str(invoice.id)
        expected = self._versions.expected(entity_id)
        self._session.stage(
            key=invoice_key(invoice.id),
            item=mappers.invoice_to_item(invoice, version=expected + 1),
            condition=_VERSION_UNCHANGED,
            values={":expected_version": expected},
            entity="invoice",
            entity_id=entity_id,
        )
        self._versions.bump(entity_id)

    def find_by_idempotency_key(self, key: str) -> Invoice | None:
        """鍵の項目を引いてから請求書を引く。読み取り 2 回。

        SQL なら索引 1 本で済むところが 2 往復になる。GSI を 1 本足せば 1 回に
        できるが、冪等キーの項目は一意制約のためにどのみち必要なので、索引を
        増やさずに済ませている。
        """
        pointer = self._session.get(idempotency_key(key))
        if pointer is None:
            return None
        invoice_id = pointer.get("invoice_id")
        if invoice_id is None:
            raise DuplicateEntity(f"idempotency item {key!r} has no invoice_id")
        return self.get(InvoiceId(str(invoice_id)))

    def list_unsettled(self, *, issued_before: datetime, limit: int = 100) -> list[Invoice]:
        items = self._session.query(
            index=GSI1,
            key_condition="gsi1pk = :unsettled AND gsi1sk <= :before",
            values={
                ":unsettled": UNSETTLED_PARTITION,
                ":before": require_iso(issued_before, field="issued_before"),
            },
            limit=limit,
        )
        return [self._track(item) for item in items]

    def list_for_customer(self, customer_id: CustomerId) -> list[Invoice]:
        items = self._session.query(
            index=GSI2,
            key_condition="gsi2pk = :customer",
            values={":customer": customer_invoices_partition(customer_id)},
            limit=100,
        )
        return [self._track(item) for item in items]

    def _track(self, item: dict[str, Any]) -> Invoice:
        self._versions.remember(item["id"], int(item["version"]))
        return mappers.item_to_invoice(item)

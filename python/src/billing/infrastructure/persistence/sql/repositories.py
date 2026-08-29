"""SQLAlchemy Core によるリポジトリ実装。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Connection, and_, select
from sqlalchemy.exc import IntegrityError

from billing.domain.ids import CustomerId, InvoiceId, PlanId, SubscriptionId
from billing.domain.invoice import Invoice
from billing.domain.plan import Plan
from billing.domain.subscription import Subscription, SubscriptionStatus
from billing.infrastructure.persistence.errors import DuplicateEntity
from billing.infrastructure.persistence.sql import mappers
from billing.infrastructure.persistence.sql.schema import (
    invoice_lines,
    invoices,
    plans,
    subscriptions,
)
from billing.infrastructure.persistence.timestamps import to_iso
from billing.infrastructure.persistence.versioning import VersionTracker

#: 更新バッチの対象になりうる状態。解約済みは二度と対象にならない。
_LIVE_STATUSES = (
    str(SubscriptionStatus.ACTIVE),
    str(SubscriptionStatus.TRIALING),
    str(SubscriptionStatus.PAST_DUE),
)


class SqlPlanRepository:
    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def get(self, plan_id: PlanId) -> Plan | None:
        row = self._conn.execute(select(plans).where(plans.c.id == str(plan_id))).one_or_none()
        return None if row is None else mappers.row_to_plan(row)

    def list_all(self) -> list[Plan]:
        rows = self._conn.execute(select(plans).order_by(plans.c.id)).all()
        return [mappers.row_to_plan(row) for row in rows]

    def add(self, plan: Plan) -> None:
        try:
            self._conn.execute(plans.insert().values(**mappers.plan_to_row(plan)))
        except IntegrityError as exc:
            raise DuplicateEntity(f"plan {plan.id!r} already exists") from exc


class SqlSubscriptionRepository:
    def __init__(self, connection: Connection) -> None:
        self._conn = connection
        self._versions = VersionTracker("subscription")

    def get(self, subscription_id: SubscriptionId) -> Subscription | None:
        row = self._conn.execute(
            select(subscriptions).where(subscriptions.c.id == str(subscription_id))
        ).one_or_none()
        if row is None:
            return None
        self._versions.remember(row.id, row.version)
        return mappers.row_to_subscription(row)

    def add(self, subscription: Subscription) -> None:
        try:
            self._conn.execute(
                subscriptions.insert().values(
                    **mappers.subscription_to_row(subscription), version=1
                )
            )
        except IntegrityError as exc:
            # SQLAlchemy 固有の例外をここで止める。上の層に IntegrityError が
            # 漏れると、ユースケースが SQLAlchemy を import する羽目になる。
            raise DuplicateEntity(f"subscription {subscription.id!r} already exists") from exc
        self._versions.remember(str(subscription.id), 1)

    def save(self, subscription: Subscription) -> None:
        entity_id = str(subscription.id)
        expected = self._versions.expected(entity_id)
        result = self._conn.execute(
            subscriptions.update()
            .where(
                and_(
                    subscriptions.c.id == entity_id,
                    subscriptions.c.version == expected,
                )
            )
            .values(**mappers.subscription_to_row(subscription), version=expected + 1)
        )
        if result.rowcount != 1:
            # 誰かが先に更新した。ここで黙って上書きすると、相手の変更が消える。
            raise self._versions.conflict(entity_id)
        self._versions.bump(entity_id)

    def list_due(self, at: datetime, *, limit: int = 100) -> list[Subscription]:
        rows = self._conn.execute(
            select(subscriptions)
            .where(
                and_(
                    subscriptions.c.status.in_(_LIVE_STATUSES),
                    subscriptions.c.period_end <= to_iso(at),
                )
            )
            .order_by(subscriptions.c.period_end)
            .limit(limit)
        ).all()
        for row in rows:
            self._versions.remember(row.id, row.version)
        return [mappers.row_to_subscription(row) for row in rows]

    def list_past_due(self, *, limit: int = 100) -> list[Subscription]:
        rows = self._conn.execute(
            select(subscriptions)
            .where(subscriptions.c.status == str(SubscriptionStatus.PAST_DUE))
            .order_by(subscriptions.c.past_due_since)
            .limit(limit)
        ).all()
        for row in rows:
            self._versions.remember(row.id, row.version)
        return [mappers.row_to_subscription(row) for row in rows]


class SqlInvoiceRepository:
    def __init__(self, connection: Connection) -> None:
        self._conn = connection
        self._versions = VersionTracker("invoice")

    def get(self, invoice_id: InvoiceId) -> Invoice | None:
        row = self._conn.execute(
            select(invoices).where(invoices.c.id == str(invoice_id))
        ).one_or_none()
        if row is None:
            return None
        self._versions.remember(row.id, row.version)
        return self._hydrate(row)

    def add(self, invoice: Invoice) -> None:
        try:
            self._conn.execute(
                invoices.insert().values(**mappers.invoice_to_row(invoice), version=1)
            )
        except IntegrityError as exc:
            # idempotency_key の UNIQUE 制約に当たった場合もここに来る。
            raise DuplicateEntity(
                f"invoice {invoice.id!r} or idempotency key "
                f"{invoice.idempotency_key!r} already exists"
            ) from exc
        line_rows = mappers.invoice_line_rows(invoice)
        if line_rows:
            self._conn.execute(invoice_lines.insert(), line_rows)
        self._versions.remember(str(invoice.id), 1)

    def save(self, invoice: Invoice) -> None:
        # 明細は発行時に確定し、以後変化しない（Invoice に明細を足すメソッドはない）。
        # だから save では見出し行だけを更新する。もし明細が可変になったら、ここは
        # 全削除して入れ直す形に変える必要がある。
        entity_id = str(invoice.id)
        expected = self._versions.expected(entity_id)
        result = self._conn.execute(
            invoices.update()
            .where(and_(invoices.c.id == entity_id, invoices.c.version == expected))
            .values(**mappers.invoice_to_row(invoice), version=expected + 1)
        )
        if result.rowcount != 1:
            raise self._versions.conflict(entity_id)
        self._versions.bump(entity_id)

    def find_by_idempotency_key(self, key: str) -> Invoice | None:
        row = self._conn.execute(
            select(invoices).where(invoices.c.idempotency_key == key)
        ).one_or_none()
        if row is None:
            return None
        self._versions.remember(row.id, row.version)
        return self._hydrate(row)

    def list_for_customer(self, customer_id: CustomerId) -> list[Invoice]:
        rows = self._conn.execute(
            select(invoices)
            .where(invoices.c.customer_id == str(customer_id))
            .order_by(invoices.c.issued_at)
        ).all()
        for row in rows:
            self._versions.remember(row.id, row.version)
        return [self._hydrate(row) for row in rows]

    def _hydrate(self, row: Any) -> Invoice:
        invoice_id = row.id
        line_rows = self._conn.execute(
            select(invoice_lines)
            .where(invoice_lines.c.invoice_id == invoice_id)
            .order_by(invoice_lines.c.position)
        ).all()
        return mappers.row_to_invoice(row, list(line_rows))

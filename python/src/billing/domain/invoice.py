"""請求書集約。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from billing.domain.errors import IllegalTransition, InvariantViolation
from billing.domain.ids import CustomerId, InvoiceId, SubscriptionId
from billing.domain.money import Money
from billing.domain.time import ensure_aware


class InvoiceStatus(StrEnum):
    OPEN = "open"
    """発行済みで、支払い待ち。"""

    PAID = "paid"
    """支払い済み。"""

    UNCOLLECTIBLE = "uncollectible"
    """回収不能として締めた。会計上は貸倒れ。"""

    VOID = "void"
    """誤って発行したので無効化した。金額は残るが債権としては存在しない。"""


@dataclass(frozen=True, slots=True)
class InvoiceLine:
    """請求書の明細 1 行。金額は負にもなりうる（返金・クレジット）。"""

    description: str
    amount: Money

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise InvariantViolation("invoice line needs a description")


@dataclass(slots=True)
class Invoice:
    """請求書 1 通。

    Subscription とは別の集約にしている。発行済みの請求書は契約の現在の状態とは
    独立した記録であり、契約を解約したからといって過去の請求書が消えては困る。
    ライフサイクルが違うものは別の集約にする。
    """

    id: InvoiceId
    customer_id: CustomerId
    subscription_id: SubscriptionId
    lines: list[InvoiceLine]
    currency: str
    status: InvoiceStatus = InvoiceStatus.OPEN
    issued_at: datetime | None = None
    paid_at: datetime | None = None
    idempotency_key: str | None = field(default=None)
    """同じ操作が二度届いたときに二重発行を防ぐための鍵。"""

    def __post_init__(self) -> None:
        for line in self.lines:
            if line.amount.currency != self.currency:
                raise InvariantViolation(
                    f"line currency {line.amount.currency} != invoice currency {self.currency}"
                )

    @classmethod
    def issue(
        cls,
        *,
        id: InvoiceId,
        customer_id: CustomerId,
        subscription_id: SubscriptionId,
        lines: list[InvoiceLine],
        currency: str,
        at: datetime,
        idempotency_key: str | None = None,
    ) -> Invoice:
        if not lines:
            raise InvariantViolation("cannot issue an invoice with no lines")
        return cls(
            id=id,
            customer_id=customer_id,
            subscription_id=subscription_id,
            lines=list(lines),
            currency=currency,
            status=InvoiceStatus.OPEN,
            issued_at=ensure_aware(at, field="at"),
            idempotency_key=idempotency_key,
        )

    @property
    def total(self) -> Money:
        total = Money.zero(self.currency)
        for line in self.lines:
            total = total + line.amount
        return total

    @property
    def is_settled(self) -> bool:
        return self.status in (InvoiceStatus.PAID, InvoiceStatus.VOID, InvoiceStatus.UNCOLLECTIBLE)

    def mark_paid(self, *, at: datetime) -> None:
        if self.status is InvoiceStatus.PAID:
            # 決済プロバイダの webhook は同じ通知を複数回送ってくる。二度目を例外に
            # すると、向こうは「失敗した」と見なして延々と再送してくる。何もしない。
            return
        if self.status is not InvoiceStatus.OPEN:
            raise IllegalTransition("invoice", self.status, "pay")
        self.status = InvoiceStatus.PAID
        self.paid_at = ensure_aware(at, field="at")

    def mark_uncollectible(self) -> None:
        if self.status is not InvoiceStatus.OPEN:
            raise IllegalTransition("invoice", self.status, "write off")
        self.status = InvoiceStatus.UNCOLLECTIBLE

    def void(self) -> None:
        if self.status is not InvoiceStatus.OPEN:
            raise IllegalTransition("invoice", self.status, "void")
        self.status = InvoiceStatus.VOID

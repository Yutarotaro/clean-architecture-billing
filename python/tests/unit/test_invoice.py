"""Invoice 集約のテスト。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from billing.domain.errors import IllegalTransition, InvariantViolation
from billing.domain.ids import CustomerId, InvoiceId, SubscriptionId
from billing.domain.invoice import Invoice, InvoiceLine, InvoiceStatus
from billing.domain.money import Money

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def issue(*lines: InvoiceLine) -> Invoice:
    return Invoice.issue(
        id=InvoiceId("inv-1"),
        customer_id=CustomerId("cus-1"),
        subscription_id=SubscriptionId("sub-1"),
        lines=list(lines),
        currency="JPY",
        at=NOW,
    )


def test_total_sums_the_lines_including_negative_ones() -> None:
    invoice = issue(
        InvoiceLine("Basic 未使用分", Money(-500)),
        InvoiceLine("Pro 残期間分", Money(1_500)),
    )

    assert invoice.total == Money(1_000)


def test_an_invoice_needs_at_least_one_line() -> None:
    with pytest.raises(InvariantViolation):
        issue()


def test_lines_must_share_the_invoice_currency() -> None:
    with pytest.raises(InvariantViolation):
        Invoice.issue(
            id=InvoiceId("inv-1"),
            customer_id=CustomerId("cus-1"),
            subscription_id=SubscriptionId("sub-1"),
            lines=[InvoiceLine("USD line", Money(10, "USD"))],
            currency="JPY",
            at=NOW,
        )


def test_marking_paid_twice_is_a_no_op() -> None:
    """webhook は同じ通知を複数回送ってくる。2 回目を例外にすると再送が止まらない。"""
    invoice = issue(InvoiceLine("Basic", Money(1_000)))
    invoice.mark_paid(at=NOW)
    first_paid_at = invoice.paid_at

    invoice.mark_paid(at=datetime(2026, 1, 2, tzinfo=UTC))

    assert invoice.status is InvoiceStatus.PAID
    assert invoice.paid_at == first_paid_at


def test_a_paid_invoice_cannot_be_voided() -> None:
    invoice = issue(InvoiceLine("Basic", Money(1_000)))
    invoice.mark_paid(at=NOW)

    with pytest.raises(IllegalTransition):
        invoice.void()
    with pytest.raises(IllegalTransition):
        invoice.mark_uncollectible()

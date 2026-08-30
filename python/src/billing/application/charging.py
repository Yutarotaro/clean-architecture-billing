"""請求書に対する決済のフロー。

複数のユースケースが同じ手順を踏むので、ここに切り出している。ドメインサービスでは
なくアプリケーションサービスなのは、「トランザクションをいつ切るか」「外部 API を
いつ叩くか」という、ビジネスルールではなく実行上の都合を扱っているため。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from billing.application.errors import EntityNotFound
from billing.application.ports import Clock, PaymentGateway, PaymentResult, UnitOfWork
from billing.domain.ids import InvoiceId
from billing.domain.invoice import Invoice
from billing.domain.subscription import Subscription

UnitOfWorkFactory = Callable[[], UnitOfWork]


@dataclass(frozen=True, slots=True)
class ChargeOutcome:
    result: PaymentResult
    invoice: Invoice
    subscription: Subscription


def charge_invoice(
    *,
    uow_factory: UnitOfWorkFactory,
    gateway: PaymentGateway,
    clock: Clock,
    invoice_id: InvoiceId,
) -> ChargeOutcome:
    """発行済みの請求書に対して決済を行い、結果を反映する。

    決済 API の呼び出しをトランザクションの外に出しているのが要点である。DB の
    トランザクションを開いたまま外部 HTTP を叩くと、相手が 30 秒応答しないあいだ
    行ロックを握り続けることになり、負荷が上がった瞬間に接続プールが枯渇する。

    そのかわり「請求書は発行できたが決済結果の反映前にプロセスが落ちる」窓が開く。
    残された open な請求書は ``SettleUnpaidInvoices`` が拾い直す。請求書には冪等キーが
    付いているので、決済が実は成功していた場合も二重課金にはならない。あの後始末が
    存在して初めて、この設計が成立する。

    決済代行との通信自体に失敗した場合は ``PaymentGatewayError`` がそのまま伝播する。
    「拒否された」のとは違い、課金できたかどうかが分からない状態なので、請求書を
    未払いとして扱ってはいけない。判断は呼び出し側に委ねる。
    """
    with uow_factory() as uow:
        invoice = _load_invoice(uow, invoice_id)
        subscription = _load_subscription(uow, invoice)
        amount = invoice.total
        customer_id = invoice.customer_id
        key = invoice.idempotency_key or str(invoice.id)
        description = f"subscription {subscription.id}"

    result = gateway.charge(
        customer_id=customer_id,
        amount=amount,
        idempotency_key=key,
        description=description,
    )

    with uow_factory() as uow:
        invoice = _load_invoice(uow, invoice_id)
        subscription = _load_subscription(uow, invoice)
        now = clock.now()
        if result.succeeded:
            invoice.mark_paid(at=now)
            subscription.mark_payment_succeeded(at=now)
        else:
            subscription.mark_payment_failed(at=now)
        uow.invoices.save(invoice)
        uow.subscriptions.save(subscription)
        uow.commit()
        return ChargeOutcome(result=result, invoice=invoice, subscription=subscription)


def _load_invoice(uow: UnitOfWork, invoice_id: InvoiceId) -> Invoice:
    invoice = uow.invoices.get(invoice_id)
    if invoice is None:
        raise EntityNotFound("invoice", invoice_id)
    return invoice


def _load_subscription(uow: UnitOfWork, invoice: Invoice) -> Subscription:
    subscription = uow.subscriptions.get(invoice.subscription_id)
    if subscription is None:
        raise EntityNotFound("subscription", invoice.subscription_id)
    return subscription

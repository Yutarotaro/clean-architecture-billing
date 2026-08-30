"""決済結果の受け取り（webhook）のユースケース。"""

from __future__ import annotations

from billing.application.charging import UnitOfWorkFactory
from billing.application.dto import InvoiceView, PaymentNotification
from billing.application.errors import EntityNotFound
from billing.application.ports import Clock
from billing.domain.ids import InvoiceId


class RecordPaymentResult:
    """決済代行から届いた支払い結果を取り込む。

    このユースケースは何度呼ばれても同じ結果にならなければならない。決済代行の
    webhook は「少なくとも 1 回」しか保証しない。同じ通知が 3 回届くのは異常では
    なく通常の動作である。二重処理を防ぐ責任は受け取る側にある。
    """

    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def execute(self, notification: PaymentNotification) -> InvoiceView:
        now = self._clock.now()
        with self._uow_factory() as uow:
            invoice = uow.invoices.get(InvoiceId(notification.invoice_id))
            if invoice is None:
                raise EntityNotFound("invoice", notification.invoice_id)

            subscription = uow.subscriptions.get(invoice.subscription_id)
            if subscription is None:
                raise EntityNotFound("subscription", invoice.subscription_id)

            if notification.succeeded:
                # 二度目以降は Invoice.mark_paid が何もしない。冪等性は「呼ぶ側が
                # 状態を確認する」のではなく「呼ばれる側が守る」形にしてある。
                invoice.mark_paid(at=now)
                subscription.mark_payment_succeeded(at=now)
            elif invoice.is_settled:
                # 決着済みの請求書に対する失敗通知は、順序が入れ替わって遅れて届いた
                # 古い通知である。webhook は配信順序を保証しないので、これは異常では
                # なく通常の動作。ここで past_due に落とすと、支払い済みの顧客が
                # 「未払い」として猶予期間ののちに解約される。
                pass
            else:
                subscription.mark_payment_failed(at=now)

            uow.invoices.save(invoice)
            uow.subscriptions.save(subscription)
            uow.commit()
            return InvoiceView.of(invoice)

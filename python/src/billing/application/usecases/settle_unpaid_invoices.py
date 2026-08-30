"""決済結果の反映漏れを拾い直すユースケース。"""

from __future__ import annotations

from datetime import timedelta

from billing.application.charging import UnitOfWorkFactory, charge_invoice
from billing.application.dto import SettlementReport
from billing.application.ports import Clock, PaymentGateway, PaymentGatewayError

#: これより新しい請求書は掴まない。いま決済中かもしれないため。
DEFAULT_SETTLEMENT_DELAY = timedelta(minutes=15)


class SettleUnpaidInvoices:
    """発行されたまま決着していない請求書に、もう一度決済を試みる。

    決済 API の呼び出しはトランザクションの外で行っている（ADR-0005）。おかげで
    外部システムの遅さが DB の接続を占有しないが、そのかわり「請求書は発行できたが
    結果を反映する前にプロセスが落ちる」窓が開く。決済代行との通信自体に失敗した
    ときも同じ状態になる。

    このユースケースがその窓を閉じる。請求書には冪等キーが付いているので、実は
    課金が成功していた場合も決済代行の側で重複排除され、二重課金にはならない。
    この後始末が存在して初めて、ADR-0005 の判断が成立する。
    """

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        gateway: PaymentGateway,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._gateway = gateway

    def execute(
        self,
        *,
        older_than: timedelta = DEFAULT_SETTLEMENT_DELAY,
        limit: int = 100,
    ) -> SettlementReport:
        cutoff = self._clock.now() - older_than

        with self._uow_factory() as uow:
            invoice_ids = [
                invoice.id
                for invoice in uow.invoices.list_unsettled(issued_before=cutoff, limit=limit)
            ]

        examined = settled = declined = unreachable = 0
        for invoice_id in invoice_ids:
            examined += 1
            try:
                outcome = charge_invoice(
                    uow_factory=self._uow_factory,
                    gateway=self._gateway,
                    clock=self._clock,
                    invoice_id=invoice_id,
                )
            except PaymentGatewayError:
                # まだ届かない。次の実行でまた拾われるので、ここでは数えるだけ。
                unreachable += 1
                continue
            if outcome.result.succeeded:
                settled += 1
            else:
                declined += 1

        return SettlementReport(
            examined=examined, settled=settled, declined=declined, unreachable=unreachable
        )

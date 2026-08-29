"""新規契約のユースケース。"""

from __future__ import annotations

from datetime import timedelta

from billing.application.charging import UnitOfWorkFactory, charge_invoice
from billing.application.dto import (
    InvoiceView,
    SubscribeCommand,
    SubscribeResult,
    SubscriptionView,
)
from billing.application.errors import EntityNotFound
from billing.application.ports import Clock, IdGenerator, PaymentGateway
from billing.domain.ids import CustomerId, InvoiceId, PlanId, SubscriptionId
from billing.domain.invoice import Invoice, InvoiceLine
from billing.domain.subscription import Subscription, SubscriptionStatus


class SubscribeToPlan:
    """顧客をプランに契約させる。試用期間なしなら初回請求まで行う。"""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        ids: IdGenerator,
        gateway: PaymentGateway,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._ids = ids
        self._gateway = gateway

    def execute(self, command: SubscribeCommand) -> SubscribeResult:
        now = self._clock.now()
        trial = timedelta(days=command.trial_days) if command.trial_days else None

        with self._uow_factory() as uow:
            plan = uow.plans.get(PlanId(command.plan_id))
            if plan is None:
                raise EntityNotFound("plan", command.plan_id)

            subscription = Subscription.subscribe(
                id=SubscriptionId(self._ids.new_id()),
                customer_id=CustomerId(command.customer_id),
                plan=plan,
                at=now,
                trial=trial,
            )
            uow.subscriptions.add(subscription)

            invoice: Invoice | None = None
            if subscription.status is SubscriptionStatus.ACTIVE:
                period = subscription.current_period
                invoice = Invoice.issue(
                    id=InvoiceId(self._ids.new_id()),
                    customer_id=subscription.customer_id,
                    subscription_id=subscription.id,
                    lines=[
                        InvoiceLine(
                            description=(
                                f"{plan.name} ({period.start:%Y-%m-%d} 〜 {period.end:%Y-%m-%d})"
                            ),
                            amount=plan.price,
                        )
                    ],
                    currency=plan.price.currency,
                    at=now,
                    # 契約 1 件につき初回請求は 1 通しかない。この形の鍵にしておけば、
                    # 同じ要求が再送されても 2 通目は作られない。
                    idempotency_key=f"initial:{subscription.id}",
                )
                uow.invoices.add(invoice)

            uow.commit()
            view = SubscriptionView.of(subscription)

        if invoice is None:
            return SubscribeResult(subscription=view, invoice=None)

        outcome = charge_invoice(
            uow_factory=self._uow_factory,
            gateway=self._gateway,
            clock=self._clock,
            invoice_id=invoice.id,
        )
        return SubscribeResult(
            subscription=SubscriptionView.of(outcome.subscription),
            invoice=InvoiceView.of(outcome.invoice),
            payment_failed=not outcome.result.succeeded,
        )

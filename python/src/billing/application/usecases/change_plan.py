"""プラン変更のユースケース。"""

from __future__ import annotations

from billing.application.charging import UnitOfWorkFactory, charge_invoice
from billing.application.dto import (
    ChangePlanCommand,
    ChangePlanResult,
    InvoiceView,
    MoneyView,
    ProrationView,
    SubscriptionView,
)
from billing.application.errors import ConflictingRequest, EntityNotFound
from billing.application.ports import Clock, IdGenerator, PaymentGateway
from billing.domain.ids import InvoiceId, PlanId, SubscriptionId
from billing.domain.invoice import Invoice, InvoiceLine
from billing.domain.plan import Plan


class ChangePlan:
    """契約中のプランを別のプランに変更し、差額を即時請求する。

    日割りの計算そのものはドメイン（``Subscription.change_plan`` と ``prorate``）が
    行う。このクラスがやっているのは、必要な集約を集めてきて、計算結果を請求書という
    別の集約に変換し、決済につなぐという「段取り」だけである。ユースケース層に
    ``if`` が並んで金額を計算し始めたら、それはドメインに書くべきものが漏れている。
    """

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

    def execute(self, command: ChangePlanCommand) -> ChangePlanResult:
        now = self._clock.now()

        with self._uow_factory() as uow:
            existing = uow.invoices.find_by_idempotency_key(command.idempotency_key)
            if existing is not None:
                if existing.subscription_id != command.subscription_id:
                    raise ConflictingRequest(
                        f"idempotency key {command.idempotency_key!r} "
                        f"was used for another subscription"
                    )
                # 同じ要求が再送された。もう一度課金してはいけない。
                subscription = uow.subscriptions.get(SubscriptionId(command.subscription_id))
                if subscription is None:
                    raise EntityNotFound("subscription", command.subscription_id)
                return ChangePlanResult(
                    subscription=SubscriptionView.of(subscription),
                    proration=_proration_view_of(existing),
                    invoice=InvoiceView.of(existing),
                )

            subscription = uow.subscriptions.get(SubscriptionId(command.subscription_id))
            if subscription is None:
                raise EntityNotFound("subscription", command.subscription_id)

            current_plan = _require_plan(uow.plans.get(subscription.plan_id), subscription.plan_id)
            new_plan = _require_plan(
                uow.plans.get(PlanId(command.new_plan_id)), command.new_plan_id
            )

            proration = subscription.change_plan(
                current_plan=current_plan, new_plan=new_plan, at=now
            )

            invoice: Invoice | None = None
            if proration.net.is_positive:
                invoice = Invoice.issue(
                    id=InvoiceId(self._ids.new_id()),
                    customer_id=subscription.customer_id,
                    subscription_id=subscription.id,
                    lines=[
                        InvoiceLine(f"{current_plan.name} 未使用分", -proration.credit),
                        InvoiceLine(f"{new_plan.name} 残期間分", proration.charge),
                    ],
                    currency=proration.net.currency,
                    at=now,
                    idempotency_key=command.idempotency_key,
                )
                uow.invoices.add(invoice)

            uow.subscriptions.save(subscription)
            uow.commit()
            subscription_view = SubscriptionView.of(subscription)
            proration_view = ProrationView.of(proration)

        if invoice is None:
            # 差額が 0 以下、つまり減額。ここでは返金も繰り越しも行わず、次回の請求
            # 期間から新しい料金が適用される（docs/design-decisions.md を参照）。
            return ChangePlanResult(
                subscription=subscription_view, proration=proration_view, invoice=None
            )

        outcome = charge_invoice(
            uow_factory=self._uow_factory,
            gateway=self._gateway,
            clock=self._clock,
            invoice_id=invoice.id,
        )
        return ChangePlanResult(
            subscription=SubscriptionView.of(outcome.subscription),
            proration=proration_view,
            invoice=InvoiceView.of(outcome.invoice),
        )


def _require_plan(plan: Plan | None, plan_id: str) -> Plan:
    if plan is None:
        raise EntityNotFound("plan", plan_id)
    return plan


def _proration_view_of(invoice: Invoice) -> ProrationView:
    """再送時に、保存済みの請求書から内訳を復元する。"""
    credit = -invoice.lines[0].amount
    charge = invoice.lines[1].amount
    net = invoice.total
    return ProrationView(
        credit=MoneyView(credit.amount, credit.currency),
        charge=MoneyView(charge.amount, charge.currency),
        net=MoneyView(net.amount, net.currency),
    )

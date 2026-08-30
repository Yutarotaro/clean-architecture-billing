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
from billing.application.ports import Clock, IdGenerator, PaymentGateway, UnitOfWork
from billing.domain.ids import InvoiceId, PlanId, SubscriptionId
from billing.domain.invoice import Invoice, InvoiceLine
from billing.domain.plan import Plan

#: クライアントが指定した冪等キーに付ける接頭辞。
#:
#: 内部で組み立てる鍵（``initial:<id>``、``renew:<id>:<iso>``）と名前空間を分ける。
#: 分けないと ``Idempotency-Key: initial:sub-1`` のような値で初回請求の請求書が
#: 引き当てられ、明細の構造が違うまま復元されて壊れる。
KEY_PREFIX = "change:"


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
        key = KEY_PREFIX + command.idempotency_key

        with self._uow_factory() as uow:
            existing = uow.invoices.find_by_idempotency_key(key)
            if existing is not None:
                # 同じ要求が再送された。もう一度課金してはいけない。
                return self._replay(uow, command, existing)

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

            # 差額が 0 以下でも請求書は必ず発行する。作らないと冪等キーを記録する
            # 場所がなくなり、再送が「すでにそのプランです」というエラーになる。
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
                idempotency_key=key,
            )
            needs_charge = proration.net.is_positive
            if not needs_charge:
                # 減額、または試用中の変更。請求するものがないので、発行と同時に
                # 決着させる。返金や次回への繰り越しは行わない
                # （docs/design-decisions.md を参照）。
                invoice.settle_without_payment(at=now)
            uow.invoices.add(invoice)

            uow.subscriptions.save(subscription)
            uow.commit()
            subscription_view = SubscriptionView.of(subscription)
            proration_view = ProrationView.of(proration)
            invoice_view = InvoiceView.of(invoice)

        if not needs_charge:
            return ChangePlanResult(
                subscription=subscription_view, proration=proration_view, invoice=invoice_view
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

    def _replay(
        self, uow: UnitOfWork, command: ChangePlanCommand, existing: Invoice
    ) -> ChangePlanResult:
        """再送に対して、保存済みの請求書から同じ結果を組み立て直す。"""
        if existing.subscription_id != command.subscription_id:
            raise ConflictingRequest(
                f"idempotency key {command.idempotency_key!r} was used for another subscription"
            )
        subscription = uow.subscriptions.get(SubscriptionId(command.subscription_id))
        if subscription is None:
            raise EntityNotFound("subscription", command.subscription_id)
        return ChangePlanResult(
            subscription=SubscriptionView.of(subscription),
            proration=_proration_view_of(existing),
            invoice=InvoiceView.of(existing),
        )


def _require_plan(plan: Plan | None, plan_id: str) -> Plan:
    if plan is None:
        raise EntityNotFound("plan", plan_id)
    return plan


def _proration_view_of(invoice: Invoice) -> ProrationView:
    """再送時に、保存済みの請求書から内訳を復元する。

    このユースケースが発行した請求書は必ず「未使用分」「残期間分」の 2 行を持つ。
    接頭辞で名前空間を分けているので他の経路の請求書が来ることはないが、
    壊れたデータに当たったときに IndexError で 500 を返さないよう検査しておく。
    """
    if len(invoice.lines) != 2:
        raise ConflictingRequest(
            f"invoice {invoice.id!r} does not look like a plan change "
            f"(expected 2 lines, found {len(invoice.lines)})"
        )
    credit = -invoice.lines[0].amount
    charge = invoice.lines[1].amount
    net = invoice.total
    return ProrationView(
        credit=MoneyView(credit.amount, credit.currency),
        charge=MoneyView(charge.amount, charge.currency),
        net=MoneyView(net.amount, net.currency),
    )

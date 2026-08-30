"""更新バッチのユースケース。"""

from __future__ import annotations

from dataclasses import dataclass

from billing.application.charging import UnitOfWorkFactory, charge_invoice
from billing.application.dto import RenewalReport
from billing.application.errors import EntityNotFound
from billing.application.ports import Clock, IdGenerator, PaymentGateway, PaymentGatewayError
from billing.domain.ids import InvoiceId, SubscriptionId
from billing.domain.invoice import Invoice, InvoiceLine


@dataclass(frozen=True, slots=True)
class _RenewOutcome:
    invoice_id: InvoiceId | None
    terminated: bool


class RenewDueSubscriptions:
    """請求期間が満了した契約を次の期間に進め、必要なら請求する。

    契約 1 件ごとにトランザクションを切る。1 万件を 1 トランザクションでまとめて
    処理すると、9,999 件目のデータ不整合で全部が巻き戻る。バッチは「途中で落ちても
    続きから再開できる」形にしておくのが基本で、そのために冪等キーを期間の開始時刻
    から決定的に組み立てている。
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

    def execute(self, *, limit: int = 100) -> RenewalReport:
        now = self._clock.now()
        renewed = invoiced = payment_failed = charge_unreachable = terminated = 0

        with self._uow_factory() as uow:
            due_ids = [s.id for s in uow.subscriptions.list_due(now, limit=limit)]

        for subscription_id in due_ids:
            outcome = self._renew_one(subscription_id)
            if outcome.terminated:
                terminated += 1
                continue
            renewed += 1
            if outcome.invoice_id is None:
                continue
            invoiced += 1
            try:
                charge = charge_invoice(
                    uow_factory=self._uow_factory,
                    gateway=self._gateway,
                    clock=self._clock,
                    invoice_id=outcome.invoice_id,
                )
            except PaymentGatewayError:
                # 決済代行に届かず、課金できたかどうかが分からない。請求書は open の
                # まま残る。ここでバッチ全体を止めると、この契約より後ろが処理されず、
                # しかも更新はすでに commit 済みなので次回の実行では is_due が偽になり、
                # 取り残された請求書を誰も拾わなくなる。1 件の失敗は 1 件に留め、
                # SettleUnpaidInvoices が後から拾い直す。
                charge_unreachable += 1
                continue
            if not charge.result.succeeded:
                payment_failed += 1

        return RenewalReport(
            renewed=renewed,
            invoiced=invoiced,
            payment_failed=payment_failed,
            charge_unreachable=charge_unreachable,
            terminated=terminated,
            canceled_for_nonpayment=self._expire_past_due(limit=limit),
        )

    def _renew_one(self, subscription_id: SubscriptionId) -> _RenewOutcome:
        # 時刻はループの外で取った値ではなく毎回取り直す。バッチが数時間かかるとき、
        # 最初に取った now で「まだ満了していない」と判定される契約が出てくる。
        now = self._clock.now()
        with self._uow_factory() as uow:
            subscription = uow.subscriptions.get(subscription_id)
            if subscription is None:
                raise EntityNotFound("subscription", subscription_id)
            plan = uow.plans.get(subscription.plan_id)
            if plan is None:
                raise EntityNotFound("plan", subscription.plan_id)

            needs_charge = subscription.renew(plan=plan, at=now)
            uow.subscriptions.save(subscription)

            if not needs_charge:
                uow.commit()
                return _RenewOutcome(invoice_id=None, terminated=True)

            period = subscription.current_period
            key = f"renew:{subscription.id}:{period.start.isoformat()}"
            existing = uow.invoices.find_by_idempotency_key(key)
            if existing is not None:
                uow.commit()
                return _RenewOutcome(invoice_id=existing.id, terminated=False)

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
                idempotency_key=key,
            )
            uow.invoices.add(invoice)
            uow.commit()
            return _RenewOutcome(invoice_id=invoice.id, terminated=False)

    def _expire_past_due(self, *, limit: int) -> int:
        """猶予を過ぎた契約を解約する。更新と同じく 1 件ずつトランザクションを切る。

        当初はここで全件をまとめて 1 トランザクションにしていた。SQL では動くが、
        DynamoDB の TransactWriteItems は 100 項目が上限なので、101 件目で落ちる。
        ``limit`` の値が永続化実装の制約と結びついてしまうのは抽象の漏れであり、
        バッチの粒度を実装に依存しない形に寄せることで解消した
        （docs/persistence-portability.md）。

        1 件ずつにすると「途中で落ちても、次の起動が続きから拾う」も同時に手に入る。
        """
        with self._uow_factory() as uow:
            past_due_ids = [s.id for s in uow.subscriptions.list_past_due(limit=limit)]

        canceled = 0
        for subscription_id in past_due_ids:
            if self._expire_one(subscription_id):
                canceled += 1
        return canceled

    def _expire_one(self, subscription_id: SubscriptionId) -> bool:
        """1 件を解約する。猶予がまだ残っていれば何もせず False を返す。"""
        now = self._clock.now()
        with self._uow_factory() as uow:
            subscription = uow.subscriptions.get(subscription_id)
            if subscription is None:
                # 一覧を取ってから今までのあいだに消えた。バッチ全体を止める理由はない。
                return False
            if not subscription.expire_if_grace_over(at=now):
                return False
            uow.subscriptions.save(subscription)
            uow.commit()
            return True

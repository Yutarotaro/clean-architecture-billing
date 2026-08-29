"""サブスクリプション集約。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from billing.domain.errors import IllegalTransition, InvariantViolation
from billing.domain.ids import CustomerId, PlanId, SubscriptionId
from billing.domain.money import Money
from billing.domain.period import BillingPeriod
from billing.domain.plan import Plan
from billing.domain.proration import Proration, prorate
from billing.domain.time import ensure_aware


class SubscriptionStatus(StrEnum):
    TRIALING = "trialing"
    """無料試用中。期間は進むが請求は起きない。"""

    ACTIVE = "active"
    """正常に課金されている。"""

    PAST_DUE = "past_due"
    """支払いに失敗した。猶予期間のあいだはサービスを止めず再試行する。"""

    CANCELED = "canceled"
    """解約済み。終端状態であり、ここから戻ることはない。"""


#: 支払い失敗から解約に至るまでの猶予。
GRACE_PERIOD = timedelta(days=14)


@dataclass(slots=True)
class Subscription:
    """契約 1 件。この集約が守る不変条件は「状態遷移が仕様どおりであること」。

    プランは ``plan_id`` で参照するだけで ``Plan`` を抱え込まない。Plan は別の集約で
    あり、ここに実体を持たせると「契約を 1 件読むためにプランも必ず読む」という結合が
    生まれる。Plan の中身が必要な操作では、ユースケースが読んで引数で渡す。
    """

    id: SubscriptionId
    customer_id: CustomerId
    plan_id: PlanId
    status: SubscriptionStatus
    current_period: BillingPeriod
    cancel_at_period_end: bool = False
    past_due_since: datetime | None = None
    canceled_at: datetime | None = None
    trial_end: datetime | None = None
    events: list[SubscriptionEvent] = field(default_factory=list, compare=False)
    """この集約に対する操作で発生した出来事。ユースケースが保存後に取り出す。"""

    @classmethod
    def subscribe(
        cls,
        *,
        id: SubscriptionId,
        customer_id: CustomerId,
        plan: Plan,
        at: datetime,
        trial: timedelta | None = None,
    ) -> Subscription:
        """新規契約を開始する。"""
        at = ensure_aware(at, field="at")
        if trial is not None and trial <= timedelta(0):
            raise InvariantViolation(f"trial must be positive when given, got {trial}")

        if trial is None:
            period = BillingPeriod.starting_at(at, plan.interval)
            status = SubscriptionStatus.ACTIVE
            trial_end = None
        else:
            # 試用期間そのものを 1 つの請求期間として扱う。試用が終わった瞬間に
            # renew が走り、そこで初めて有料の期間が始まる。
            period = BillingPeriod(at, at + trial)
            status = SubscriptionStatus.TRIALING
            trial_end = period.end

        subscription = cls(
            id=id,
            customer_id=customer_id,
            plan_id=plan.id,
            status=status,
            current_period=period,
            trial_end=trial_end,
        )
        subscription._record("subscription.created", at)
        return subscription

    @property
    def is_terminated(self) -> bool:
        return self.status is SubscriptionStatus.CANCELED

    def is_due(self, at: datetime) -> bool:
        """更新処理の対象か。解約済みは対象外。"""
        return not self.is_terminated and self.current_period.is_due(at)

    def change_plan(self, *, current_plan: Plan, new_plan: Plan, at: datetime) -> Proration:
        """プランを変更し、その期間ぶんの差額を返す。

        差額の請求書を作るのはこの集約の仕事ではない。ここが返すのは「いくらか」と
        いう事実だけで、それを請求書にするか、次回請求に繰り越すかは呼び出し側が決める。
        """
        at = ensure_aware(at, field="at")
        if self.is_terminated:
            raise IllegalTransition("subscription", self.status, "change plan of")
        if current_plan.id != self.plan_id:
            raise InvariantViolation(
                f"current_plan {current_plan.id!r} does not match "
                f"subscription plan {self.plan_id!r}"
            )
        if new_plan.id == self.plan_id:
            raise InvariantViolation(f"already on plan {new_plan.id!r}")
        if new_plan.price.currency != current_plan.price.currency:
            raise InvariantViolation("cannot change to a plan in a different currency")

        if self.status is SubscriptionStatus.TRIALING:
            # 試用中はまだ 1 円も請求していないので、返すものも取るものもない。
            zero = Money.zero(current_plan.price.currency)
            proration = Proration(credit=zero, charge=zero)
        else:
            proration = prorate(
                period=self.current_period,
                at=at,
                old_price=current_plan.price,
                new_price=new_plan.price,
            )

        self.plan_id = new_plan.id
        self._record("subscription.plan_changed", at)
        return proration

    def cancel(self, *, at: datetime, immediately: bool = False) -> None:
        """解約する。既定では期末解約（支払い済みの期間は使い切れる）。"""
        at = ensure_aware(at, field="at")
        if self.is_terminated:
            raise IllegalTransition("subscription", self.status, "cancel")

        if immediately:
            self.status = SubscriptionStatus.CANCELED
            self.canceled_at = at
            self.cancel_at_period_end = False
            self._record("subscription.canceled", at)
        else:
            self.cancel_at_period_end = True
            self._record("subscription.cancel_scheduled", at)

    def mark_payment_failed(self, *, at: datetime) -> None:
        at = ensure_aware(at, field="at")
        if self.status not in (SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE):
            raise IllegalTransition("subscription", self.status, "mark payment failed on")
        if self.status is SubscriptionStatus.PAST_DUE:
            # すでに延滞中。猶予の起点は最初の失敗のままにする。ここを更新してしまうと
            # 再試行が走るたびに猶予が延び、永遠に解約されない契約ができる。
            return
        self.status = SubscriptionStatus.PAST_DUE
        self.past_due_since = at
        self._record("subscription.payment_failed", at)

    def mark_payment_succeeded(self, *, at: datetime) -> None:
        at = ensure_aware(at, field="at")
        if self.is_terminated:
            raise IllegalTransition("subscription", self.status, "mark payment succeeded on")
        self.status = SubscriptionStatus.ACTIVE
        self.past_due_since = None
        self._record("subscription.payment_succeeded", at)

    def expire_if_grace_over(self, *, at: datetime, grace: timedelta = GRACE_PERIOD) -> bool:
        """延滞したまま猶予を過ぎていれば解約する。解約したら True。"""
        at = ensure_aware(at, field="at")
        if self.status is not SubscriptionStatus.PAST_DUE or self.past_due_since is None:
            return False
        if at - self.past_due_since < grace:
            return False
        self.status = SubscriptionStatus.CANCELED
        self.canceled_at = at
        self._record("subscription.canceled_for_nonpayment", at)
        return True

    def renew(self, *, plan: Plan, at: datetime) -> bool:
        """請求期間を次に進める。次の期間で課金が必要なら True を返す。

        期末解約が予約されていればここで終端に落とす。「期末解約」を状態として持たず
        フラグで持つのは、解約予約の取り消しを状態遷移ではなくフラグの解除として
        扱えるようにするためである。
        """
        at = ensure_aware(at, field="at")
        if self.is_terminated:
            raise IllegalTransition("subscription", self.status, "renew")
        if not self.current_period.is_due(at):
            raise InvariantViolation(
                f"period {self.current_period.end.isoformat()} is not due at {at.isoformat()}"
            )
        if plan.id != self.plan_id:
            raise InvariantViolation(
                f"plan {plan.id!r} does not match subscription {self.plan_id!r}"
            )

        if self.cancel_at_period_end:
            self.status = SubscriptionStatus.CANCELED
            self.canceled_at = self.current_period.end
            self._record("subscription.canceled", at)
            return False

        # 新しい期間の起点は「今」ではなく「前の期間の終わり」。バッチが数時間遅れて
        # 動いても請求日がずれないようにするための、地味だが重要な一行。
        self.current_period = BillingPeriod.starting_at(self.current_period.end, plan.interval)
        self.status = SubscriptionStatus.ACTIVE
        self._record("subscription.renewed", at)
        return True

    def _record(self, name: str, at: datetime) -> None:
        self.events.append(SubscriptionEvent(name=name, subscription_id=self.id, occurred_at=at))

    def pull_events(self) -> list[SubscriptionEvent]:
        """溜まった出来事を取り出して空にする。"""
        drained = list(self.events)
        self.events.clear()
        return drained


@dataclass(frozen=True, slots=True)
class SubscriptionEvent:
    """契約に起きた出来事。

    「メールを送る」「Slack に通知する」といった副作用をドメインに書かないための逃げ道。
    ドメインは起きたことを記録するだけで、それを何に使うかは外側が決める。
    """

    name: str
    subscription_id: SubscriptionId
    occurred_at: datetime

"""料金プラン。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from billing.domain.errors import InvariantViolation
from billing.domain.ids import PlanId
from billing.domain.money import Money
from billing.domain.time import add_months


class BillingInterval(StrEnum):
    """課金サイクル。"""

    MONTHLY = "monthly"
    YEARLY = "yearly"

    def next_after(self, start: datetime) -> datetime:
        """``start`` を起点とした次の請求日を返す。"""
        match self:
            case BillingInterval.MONTHLY:
                return add_months(start, 1)
            case BillingInterval.YEARLY:
                return add_months(start, 12)


@dataclass(frozen=True, slots=True)
class Plan:
    """契約できる料金プラン。不変。

    価格改定は「既存プランの price を書き換える」のではなく「新しい Plan を作って
    以後の契約をそちらに向ける」と考える。過去に発行済みの請求書の金額が、後から
    プランを編集したせいで変わってしまう事故を構造的に防ぐ。
    """

    id: PlanId
    name: str
    price: Money
    interval: BillingInterval

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise InvariantViolation("plan name must not be empty")
        if self.price.is_negative:
            raise InvariantViolation(f"plan price must not be negative, got {self.price}")

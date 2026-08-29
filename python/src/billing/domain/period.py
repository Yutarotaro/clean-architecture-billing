"""請求期間。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction

from billing.domain.errors import InvariantViolation
from billing.domain.plan import BillingInterval
from billing.domain.time import ensure_aware


@dataclass(frozen=True, slots=True)
class BillingPeriod:
    """``[start, end)`` の半開区間。終端は含まない。

    半開区間にしておくと、ある期間の end と次の期間の start が同じ値になり、隙間も
    重複も生まれない。「23:59:59 まで」のような書き方をすると、閏秒やミリ秒の扱いで
    必ずどこかに穴が空く。
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", ensure_aware(self.start, field="start"))
        object.__setattr__(self, "end", ensure_aware(self.end, field="end"))
        if self.start >= self.end:
            raise InvariantViolation(f"period must be non-empty: {self.start} >= {self.end}")

    @classmethod
    def starting_at(cls, start: datetime, interval: BillingInterval) -> BillingPeriod:
        start = ensure_aware(start, field="start")
        return cls(start, interval.next_after(start))

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def contains(self, at: datetime) -> bool:
        at = ensure_aware(at, field="at")
        return self.start <= at < self.end

    def is_due(self, at: datetime) -> bool:
        """``at`` の時点でこの期間が満了しているか。"""
        return ensure_aware(at, field="at") >= self.end

    def remaining_ratio(self, at: datetime) -> Fraction:
        """``at`` の時点で残っている期間の割合を厳密な分数で返す。

        float を経由しない。1/3 のような比率を float にすると、掛けたあとの丸めが
        処理系依存になり、テストが「なぜか 1 円ずれる」形で落ちるようになる。
        """
        at = ensure_aware(at, field="at")
        if at <= self.start:
            return Fraction(1)
        if at >= self.end:
            return Fraction(0)
        return Fraction(
            int((self.end - at).total_seconds() * 1_000_000),
            int(self.duration.total_seconds() * 1_000_000),
        )

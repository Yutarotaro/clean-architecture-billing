"""請求期間。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction

from billing.domain.errors import InvariantViolation
from billing.domain.plan import BillingInterval
from billing.domain.time import ensure_aware


def _microseconds(delta: timedelta) -> int:
    """``timedelta`` を整数のマイクロ秒に変換する。

    ``total_seconds()`` は float を返すため、大きな期間や μ秒の端数で精度が落ちる。
    日・秒・マイクロ秒はいずれも整数で保持されているので、そこから組み立てれば厳密。
    """
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


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

        float を一度も経由しない。``timedelta.total_seconds()`` は float を返すので、
        マイクロ秒の端数を持つ期間では往復で 1μ秒ぶんずれる。1/3 のような比率を
        float にすると、掛けたあとの丸めが処理系依存になり、テストが「なぜか 1 円
        ずれる」形で落ちるようになる。``timedelta`` が内部に持つ日・秒・マイクロ秒は
        すべて整数なので、そこから直接組み立てる。
        """
        at = ensure_aware(at, field="at")
        if at <= self.start:
            return Fraction(1)
        if at >= self.end:
            return Fraction(0)
        return Fraction(_microseconds(self.end - at), _microseconds(self.duration))

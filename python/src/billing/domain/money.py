"""金額を表す値オブジェクト。"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from billing.domain.errors import CurrencyMismatch, InvariantViolation


@dataclass(frozen=True, slots=True)
class Money:
    """通貨の最小単位を整数で保持する。

    金額に float を使わない。0.1 + 0.2 != 0.3 の世界で請求書を作ると、丸め誤差が
    そのまま会計上の差異になる。日本円なら 1 円、米ドルならセントを ``amount`` に入れる。
    """

    amount: int
    currency: str = "JPY"

    def __post_init__(self) -> None:
        if not isinstance(self.amount, int) or isinstance(self.amount, bool):
            raise InvariantViolation(f"amount must be an int in minor units, got {self.amount!r}")
        if len(self.currency) != 3 or not self.currency.isalpha() or not self.currency.isupper():
            raise InvariantViolation(
                f"currency must be an ISO 4217 alpha code, got {self.currency!r}"
            )

    @classmethod
    def zero(cls, currency: str = "JPY") -> Money:
        return cls(0, currency)

    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    @property
    def is_positive(self) -> bool:
        return self.amount > 0

    @property
    def is_negative(self) -> bool:
        return self.amount < 0

    def _assert_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(self.currency, other.currency)

    def __add__(self, other: Money) -> Money:
        self._assert_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._assert_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __mul__(self, factor: int) -> Money:
        if not isinstance(factor, int) or isinstance(factor, bool):
            raise InvariantViolation("Money can only be multiplied by an int")
        return Money(self.amount * factor, self.currency)

    def __lt__(self, other: Money) -> bool:
        self._assert_same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._assert_same_currency(other)
        return self.amount <= other.amount

    def scale(self, ratio: Fraction) -> Money:
        """比率を掛けて最小単位に丸める。

        丸めは切り捨て（floor）で統一する。日割りでは事業者側ではなく利用者側に
        有利な方向に倒す、という方針をここで一箇所に閉じ込めている。方針を変えたく
        なったら変えるのはこのメソッドだけで済む。
        """
        if ratio < 0:
            raise InvariantViolation(f"ratio must not be negative, got {ratio}")
        scaled = Fraction(self.amount) * ratio
        return Money(scaled.numerator // scaled.denominator, self.currency)

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"

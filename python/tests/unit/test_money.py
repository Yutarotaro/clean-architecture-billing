"""Money の単体テスト。"""

from __future__ import annotations

from fractions import Fraction

import pytest

from billing.domain.errors import CurrencyMismatch, InvariantViolation
from billing.domain.money import Money


def test_arithmetic_keeps_currency() -> None:
    assert Money(1_000) + Money(500) == Money(1_500)
    assert Money(1_000) - Money(1_500) == Money(-500)
    assert Money(1_000) * 3 == Money(3_000)


def test_different_currencies_cannot_be_mixed() -> None:
    with pytest.raises(CurrencyMismatch):
        Money(1_000, "JPY") + Money(10, "USD")


def test_amount_must_be_an_integer_in_minor_units() -> None:
    """float を弾く。ここを緩めると丸め誤差が請求金額に混ざる。"""
    with pytest.raises(InvariantViolation):
        Money(1000.5)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("amount", "ratio", "expected"),
    [
        (3_000, Fraction(1, 2), 1_500),
        (3_000, Fraction(1, 3), 1_000),
        # 1000 * 1/3 = 333.33... → 切り捨てで 333。事業者ではなく利用者に有利な側へ倒す。
        (1_000, Fraction(1, 3), 333),
        (1_000, Fraction(2, 3), 666),
        (999, Fraction(1, 2), 499),
        (1_000, Fraction(0), 0),
        (1_000, Fraction(1), 1_000),
    ],
)
def test_scale_floors(amount: int, ratio: Fraction, expected: int) -> None:
    assert Money(amount).scale(ratio) == Money(expected)


@pytest.mark.parametrize(
    ("amount", "ratio", "expected"),
    [
        # 負の金額でも floor で丸める。Go 版と結果を揃えるための取り決め。
        # 0 方向への切り捨て（truncate）だと -333 になり、言語ごとに違う金額が出る。
        (-1_000, Fraction(1, 3), -334),
        (-1_000, Fraction(2, 3), -667),
        (-999, Fraction(1, 2), -500),
        (-3_000, Fraction(1, 2), -1_500),
    ],
)
def test_scale_of_a_negative_amount_floors(amount: int, ratio: Fraction, expected: int) -> None:
    """負の金額の丸め方向。プラン変更の明細では credit を負で持つため実際に通る。"""
    assert Money(amount).scale(ratio) == Money(expected)


def test_scale_rejects_negative_ratio() -> None:
    with pytest.raises(InvariantViolation):
        Money(1_000).scale(Fraction(-1, 2))


def test_currency_must_be_an_iso_code() -> None:
    with pytest.raises(InvariantViolation):
        Money(100, "jpy")
    with pytest.raises(InvariantViolation):
        Money(100, "JPYEN")

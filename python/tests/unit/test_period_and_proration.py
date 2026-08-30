"""請求期間と日割り計算の単体テスト。

外部依存が一切ないので、表を書くだけでテストになる。日割りのようにビジネス上の
争点になりやすい計算をこの形で書けることが、ドメイン層を独立させる実利である。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest

from billing.domain.errors import InvariantViolation
from billing.domain.money import Money
from billing.domain.period import BillingPeriod
from billing.domain.plan import BillingInterval
from billing.domain.proration import prorate
from billing.domain.time import add_months

JAN = datetime(2026, 1, 1, tzinfo=UTC)
FEB = datetime(2026, 2, 1, tzinfo=UTC)


def test_period_must_be_timezone_aware() -> None:
    with pytest.raises(InvariantViolation):
        BillingPeriod(datetime(2026, 1, 1), FEB)  # noqa: DTZ001


def test_period_must_be_non_empty() -> None:
    with pytest.raises(InvariantViolation):
        BillingPeriod(FEB, JAN)


def test_period_is_half_open() -> None:
    """終端は含まない。次の期間の開始と重ならないようにするため。"""
    period = BillingPeriod(JAN, FEB)
    assert period.contains(JAN)
    assert not period.contains(FEB)
    assert period.is_due(FEB)
    assert not period.is_due(FEB - timedelta(microseconds=1))


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        (JAN, Fraction(1)),
        (datetime(2026, 1, 16, 12, tzinfo=UTC), Fraction(1, 2)),
        (FEB, Fraction(0)),
        (FEB + timedelta(days=10), Fraction(0)),
    ],
)
def test_remaining_ratio(at: datetime, expected: Fraction) -> None:
    assert BillingPeriod(JAN, FEB).remaining_ratio(at) == expected


def test_remaining_ratio_is_exact_for_sub_second_periods() -> None:
    """マイクロ秒の端数を持つ期間でも、比率が厳密であること。

    ``timedelta.total_seconds()`` は float を返すので、そこを経由すると 1μ秒ぶん
    ずれる。金額に化けるのは丸めの境界に乗ったときだけなので、テストで固定しておく。
    """
    start = datetime(2024, 2, 16, 22, 22, 56, 730_795, tzinfo=UTC)
    end = datetime(2024, 9, 2, 17, 11, 42, 136_530, tzinfo=UTC)
    at = datetime(2024, 5, 24, 17, 39, 38, 811_523, tzinfo=UTC)

    ratio = BillingPeriod(start, end).remaining_ratio(at)

    expected = Fraction(
        (end - at) // timedelta(microseconds=1),
        (end - start) // timedelta(microseconds=1),
    )
    assert ratio == expected


def test_proration_credits_the_unused_part_and_charges_the_new_plan() -> None:
    """1 月の折り返し地点で 1,000 円プランから 3,000 円プランへ変えた場合。

    31 日の期間のちょうど半分（15.5 日）が残っているので、旧プランの 500 円を返し、
    新プランの 1,500 円を請求する。差し引き 1,000 円。
    """
    proration = prorate(
        period=BillingPeriod(JAN, FEB),
        at=datetime(2026, 1, 16, 12, tzinfo=UTC),
        old_price=Money(1_000),
        new_price=Money(3_000),
    )

    assert proration.credit == Money(500)
    assert proration.charge == Money(1_500)
    assert proration.net == Money(1_000)


def test_downgrade_produces_a_negative_net() -> None:
    proration = prorate(
        period=BillingPeriod(JAN, FEB),
        at=datetime(2026, 1, 16, 12, tzinfo=UTC),
        old_price=Money(3_000),
        new_price=Money(1_000),
    )

    assert proration.net == Money(-1_000)


def test_proration_at_the_very_start_charges_the_full_difference() -> None:
    proration = prorate(
        period=BillingPeriod(JAN, FEB),
        at=JAN,
        old_price=Money(1_000),
        new_price=Money(3_000),
    )
    assert proration.net == Money(2_000)


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (datetime(2026, 1, 31, tzinfo=UTC), 1, datetime(2026, 2, 28, tzinfo=UTC)),
        (datetime(2024, 1, 31, tzinfo=UTC), 1, datetime(2024, 2, 29, tzinfo=UTC)),
        (datetime(2026, 1, 31, tzinfo=UTC), 12, datetime(2027, 1, 31, tzinfo=UTC)),
        (datetime(2026, 12, 15, tzinfo=UTC), 1, datetime(2027, 1, 15, tzinfo=UTC)),
    ],
)
def test_add_months_clamps_to_the_end_of_month(
    start: datetime, months: int, expected: datetime
) -> None:
    """1/31 の 1 か月後は 2/28。存在しない日付を作らないための丸め。"""
    assert add_months(start, months) == expected


def test_interval_drives_the_next_period() -> None:
    monthly = BillingPeriod.starting_at(JAN, BillingInterval.MONTHLY)
    yearly = BillingPeriod.starting_at(JAN, BillingInterval.YEARLY)

    assert monthly.end == FEB
    assert yearly.end == datetime(2027, 1, 1, tzinfo=UTC)

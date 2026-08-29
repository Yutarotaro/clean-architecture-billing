"""ドメインが扱う時刻の規約。

内部で扱う ``datetime`` は必ず tz-aware で、かつ UTC に正規化する。ローカル時刻の
まま計算すると、夏時間の切り替え日に請求期間が 23 時間や 25 時間になる。
"""

from __future__ import annotations

import calendar
from datetime import UTC, datetime

from billing.domain.errors import InvariantViolation


def ensure_aware(value: datetime, *, field: str) -> datetime:
    """tz-aware であることを確かめ、UTC に正規化して返す。"""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise InvariantViolation(f"{field} must be timezone-aware, got naive {value.isoformat()}")
    return value.astimezone(UTC)


def add_months(value: datetime, months: int) -> datetime:
    """月を加算する。

    1/31 の 1 か月後は 2/28（閏年なら 2/29）とする。日付をそのまま足すと存在しない
    日ができるため、月末に丸める。この「月末の扱い」は課金サイクルの仕様そのもので
    あり、ライブラリ任せにせずドメインに書いて明示する。
    """
    total = (value.year * 12 + (value.month - 1)) + months
    year, month = divmod(total, 12)
    month += 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)

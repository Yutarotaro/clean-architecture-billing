"""Clock ポートの実装。"""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """本番用。OS の時計を UTC で返す。"""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """テスト用。時刻を止めたり、任意に進めたりできる。

    テスト専用のコードを src 配下に置いているのは、これが「Clock ポートの実装の一つ」
    であってテストの都合ではないから。CLI から特定日時のバッチを再現するときにも使える。
    """

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware datetime")
        self._at = at.astimezone(UTC)

    def now(self) -> datetime:
        return self._at

    def set(self, at: datetime) -> None:
        self._at = at.astimezone(UTC)

    def advance(self, **kwargs: float) -> datetime:
        from datetime import timedelta

        self._at = self._at + timedelta(**kwargs)
        return self._at

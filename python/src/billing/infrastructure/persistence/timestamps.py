"""永続化層で共通に使う時刻の表現。

どの実装でも「UTC の固定長 ISO 8601 文字列」で保存する。SQLite の DATETIME も
DynamoDB の N 型も、そのままではタイムゾーンを保持できない。文字列にしておけば
辞書順が時刻順と一致し、範囲クエリもソートキーもそのまま使える。
"""

from __future__ import annotations

from datetime import UTC, datetime


def to_iso(value: datetime | None) -> str | None:
    """UTC の固定長 ISO 8601 文字列にする。

    ``timespec="microseconds"`` で桁数を固定しているのは、文字列の辞書順を時刻順と
    一致させるため。これが崩れると範囲クエリが静かに嘘をつく。
    """
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def require_iso(value: datetime, *, field: str) -> str:
    result = to_iso(value)
    if result is None:  # pragma: no cover - 型の都合上の分岐
        raise ValueError(f"{field} must not be None")
    return result


def from_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"stored timestamp lost its timezone: {value!r}")
    return parsed.astimezone(UTC)


def require_from_iso(value: str | None, *, field: str) -> datetime:
    parsed = from_iso(value)
    if parsed is None:
        raise ValueError(f"{field} must not be null in the datastore")
    return parsed

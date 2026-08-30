"""永続化層で共通に使う時刻の表現。

どの実装でも「UTC の固定長 ISO 8601 文字列」で保存する。SQLite の DATETIME も
DynamoDB の N 型も、そのままではタイムゾーンを保持できない。文字列にしておけば
辞書順が時刻順と一致し、範囲クエリもソートキーもそのまま使える。
"""

from __future__ import annotations

from datetime import UTC, datetime

#: 保存する時刻の書式。Go 版（infra/storage/timestamps.go）と同じにしてある。
#:
#: ``isoformat()`` は末尾を "+00:00" にするが、Go の RFC3339 系は "Z" を使う。
#: 単独ではどちらでも動くものの、両方の実装が同じ SQLite ファイルを触ると、
#: 片方が相手の行をパースできず、しかも "+"(0x2B) < "Z"(0x5A) なので範囲クエリが
#: 静かに嘘をつく。揃えておく。
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def to_iso(value: datetime | None) -> str | None:
    """UTC の固定長 ISO 8601 文字列にする。

    桁数を固定しているのは、文字列の辞書順を時刻順と一致させるため。
    これが崩れると範囲クエリが静かに嘘をつく。
    """
    if value is None:
        return None
    return value.astimezone(UTC).strftime(TIME_FORMAT)


def require_iso(value: datetime, *, field: str) -> str:
    result = to_iso(value)
    if result is None:  # pragma: no cover - 型の都合上の分岐
        raise ValueError(f"{field} must not be None")
    return result


def from_iso(value: str | None) -> datetime | None:
    """保存された文字列を時刻に戻す。

    ``fromisoformat`` は "Z" も "+00:00" も受け付けるので、書式を変える前に
    書かれた行もそのまま読める。
    """
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

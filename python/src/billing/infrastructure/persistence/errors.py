"""永続化層の実装が使う例外。

ユースケースが捕まえることを想定していない。捕まえるべきもの（楽観ロックの衝突）は
application/errors.py の ``ConcurrencyConflict`` として定義してある。ここにあるのは
「呼び出し方が間違っている」を早期に知らせるためのもので、握り潰さずに落とす。
"""

from __future__ import annotations


class DuplicateEntity(RuntimeError):
    """すでに存在する ID、またはすでに使われた冪等キーで作ろうとした。"""


class UnknownEntity(RuntimeError):
    """存在しない集約を save しようとした。"""

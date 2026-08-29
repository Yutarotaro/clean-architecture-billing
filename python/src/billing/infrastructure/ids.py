"""IdGenerator ポートの実装。"""

from __future__ import annotations

import uuid
from itertools import count


class UuidGenerator:
    """本番用。"""

    def new_id(self) -> str:
        return str(uuid.uuid4())


class SequentialIdGenerator:
    """テスト用。``sub-1``, ``sub-2`` のように読める ID を返す。

    テストが落ちたときに ``a3f1c8e2-...`` が並んでいても何も分からない。連番なら
    「2 件目の請求書が作られていない」が一目で読める。
    """

    def __init__(self, prefix: str = "id") -> None:
        self._prefix = prefix
        self._counter = count(1)

    def new_id(self) -> str:
        return f"{self._prefix}-{next(self._counter)}"

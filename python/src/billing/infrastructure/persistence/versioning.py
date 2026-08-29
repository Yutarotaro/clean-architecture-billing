"""楽観ロックのバージョン追跡。

SQLite でも DynamoDB でも同じ道具を使う。「読んだときのバージョンを覚えておき、
書くときにそれが変わっていないことを条件にする」という一点だけが本質で、それを
``UPDATE ... WHERE version = ?`` で表現するか ``ConditionExpression`` で表現するかは
実装の差でしかない。

ドメインオブジェクトに ``version`` を持たせないのが方針である。楽観ロックは
永続化の都合であって、契約や請求書というビジネス概念の一部ではない。
"""

from __future__ import annotations

from billing.application.errors import ConcurrencyConflict


class VersionTracker:
    """この UnitOfWork の中で、どの ID をどのバージョンで読んだかを覚える。"""

    def __init__(self, entity: str) -> None:
        self._entity = entity
        self._versions: dict[str, int] = {}

    def remember(self, entity_id: str, version: int) -> None:
        self._versions[entity_id] = version

    def expected(self, entity_id: str) -> int:
        version = self._versions.get(entity_id)
        if version is None:
            # 読まずに更新しようとしている。この状態では「誰も触っていないこと」を
            # 保証できないので、上書きせずに落とす。
            raise ConcurrencyConflict(self._entity, entity_id)
        return version

    def bump(self, entity_id: str) -> int:
        version = self._versions.get(entity_id, 0) + 1
        self._versions[entity_id] = version
        return version

    def conflict(self, entity_id: str) -> ConcurrencyConflict:
        return ConcurrencyConflict(self._entity, entity_id)

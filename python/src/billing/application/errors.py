"""ユースケース層の例外。

ドメインの不変条件違反（DomainError）とは区別する。「その ID の契約が存在しない」は
ビジネスルールの違反ではなく、アプリケーションへの入力の問題である。
"""

from __future__ import annotations


class ApplicationError(Exception):
    pass


class EntityNotFound(ApplicationError):
    def __init__(self, entity: str, entity_id: str) -> None:
        super().__init__(f"{entity} {entity_id!r} not found")
        self.entity = entity
        self.entity_id = entity_id


class ConflictingRequest(ApplicationError):
    """同じ冪等キーで、内容の違う要求が届いた。"""


class ConcurrencyConflict(ApplicationError):
    """同じ集約を別の実行が先に更新していた。

    どの永続化技術を使っていても起きうるので、インフラ層ではなくここに置く。
    ユースケースはこれを捕まえて再試行するか、呼び出し元に 409 として返すかを選べる。

    この例外がインフラ層から投げられること自体が、抽象の漏れではないかという議論は
    ある。ただ「誰かと編集がぶつかった」はビジネス上意味のある事実であり、SQL でも
    DynamoDB でも同じ概念として存在する。技術固有の詳細（DynamoDB の
    ConditionalCheckFailedException など）はここで throw する前に落としきる。
    """

    def __init__(self, entity: str, entity_id: str) -> None:
        super().__init__(f"{entity} {entity_id!r} was modified concurrently")
        self.entity = entity
        self.entity_id = entity_id

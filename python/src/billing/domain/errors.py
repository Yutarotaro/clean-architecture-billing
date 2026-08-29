"""ドメイン層の例外。

「そのビジネスルールに反している」という事実だけを表す。HTTP のステータスコードや
DB のエラーコードといった外側の語彙をここに持ち込まない。変換は presentation 層の
仕事である。
"""

from __future__ import annotations


class DomainError(Exception):
    """ドメインの不変条件を破ろうとしたときに送出される例外の基底。"""


class CurrencyMismatch(DomainError):
    """異なる通貨どうしを計算しようとした。"""

    def __init__(self, left: str, right: str) -> None:
        super().__init__(f"currency mismatch: {left} != {right}")
        self.left = left
        self.right = right


class InvariantViolation(DomainError):
    """エンティティ・値オブジェクトの生成時点で成り立つべき条件を満たしていない。"""


class IllegalTransition(DomainError):
    """その状態からは許されない状態遷移を行おうとした。"""

    def __init__(self, entity: str, from_state: str, action: str) -> None:
        super().__init__(f"cannot {action} a {entity} in state {from_state!r}")
        self.entity = entity
        self.from_state = from_state
        self.action = action

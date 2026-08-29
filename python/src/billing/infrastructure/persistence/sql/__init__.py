"""SQLAlchemy Core を使った永続化実装。

ORM のセッションではなく Core の Connection を直接使い、行とドメインオブジェクトの
変換は自分で書いている。ドメインクラスに ORM の基底クラスやデコレータを一切付けたく
ないためで、その代償として mappers.py の手書きコードを引き受けている（ADR-0003）。
"""

from billing.infrastructure.persistence.sql.schema import METADATA, create_schema
from billing.infrastructure.persistence.sql.uow import SqlAlchemyUnitOfWork

__all__ = ["METADATA", "create_schema", "SqlAlchemyUnitOfWork"]

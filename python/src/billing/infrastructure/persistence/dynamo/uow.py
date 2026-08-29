"""DynamoDB の UnitOfWork。"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

from billing.domain.repositories import (
    InvoiceRepository,
    PlanRepository,
    SubscriptionRepository,
)
from billing.infrastructure.persistence.dynamo.repositories import (
    DynamoInvoiceRepository,
    DynamoPlanRepository,
    DynamoSubscriptionRepository,
)
from billing.infrastructure.persistence.dynamo.session import DynamoSession


class DynamoUnitOfWork:
    """``UnitOfWork`` プロトコルを DynamoDB で満たす。

    ``__enter__`` で何も始まらないのが SQL 版との最大の違いである。ここで開くのは
    「書き込みを溜める箱」だけで、データベース側には何の状態も作られない。だから
    ``rollback`` は箱を捨てるだけで済み、逆に「読み取りの一貫性」は何も保証されない。

    上の層から見えるインターフェースは同じでも、保証している内容は同じではない。
    その差がユースケースを壊さないかどうかは、契約テストで確かめている。
    """

    # 具象クラスではなく抽象型で宣言する。UnitOfWork を使う側に実装クラスの名前が
    # 見えないようにするための注釈であり、同時に UnitOfWork プロトコルを満たす条件でもある。
    subscriptions: SubscriptionRepository
    invoices: InvoiceRepository
    plans: PlanRepository

    def __init__(self, client: Any, table_name: str) -> None:
        self._client = client
        self._table_name = table_name
        self._session: DynamoSession | None = None

    def __enter__(self) -> Self:
        self._bind()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._session is not None:
            self._session.rollback()
        self._session = None

    def commit(self) -> None:
        self._require_session().commit()
        # commit 後はバージョン追跡の前提が変わるので、リポジトリを作り直す。
        self._bind()

    def rollback(self) -> None:
        self._require_session().rollback()
        self._bind()

    def _require_session(self) -> DynamoSession:
        if self._session is None:
            raise RuntimeError("UnitOfWork used outside of a with-block")
        return self._session

    def _bind(self) -> None:
        self._session = DynamoSession(self._client, self._table_name)
        self.plans = DynamoPlanRepository(self._session)
        self.subscriptions = DynamoSubscriptionRepository(self._session)
        self.invoices = DynamoInvoiceRepository(self._session)

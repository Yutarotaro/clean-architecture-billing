"""インメモリの UnitOfWork。"""

from __future__ import annotations

from copy import deepcopy
from types import TracebackType
from typing import Self

from billing.domain.repositories import (
    InvoiceRepository,
    PlanRepository,
    SubscriptionRepository,
)
from billing.infrastructure.persistence.memory.database import MemoryDatabase
from billing.infrastructure.persistence.memory.repositories import (
    InMemoryInvoiceRepository,
    InMemoryPlanRepository,
    InMemorySubscriptionRepository,
)


class InMemoryUnitOfWork:
    """作業用のコピーの上で変更し、commit されたときだけ本体に反映する。

    「テスト用だからトランザクションはなくていい」とはしない。commit を書き忘れた
    ユースケースがテストでは通って本番で壊れる、という事故がまさにここで防がれる。
    本物と同じ意味論を持つ fake でなければ、テストの意味が薄れる。
    """

    # 具象クラスではなく抽象型で宣言する。UnitOfWork を使う側に実装クラスの名前が
    # 見えないようにするための注釈であり、同時に UnitOfWork プロトコルを満たす条件でもある。
    subscriptions: SubscriptionRepository
    invoices: InvoiceRepository
    plans: PlanRepository

    def __init__(self, db: MemoryDatabase) -> None:
        self._db = db
        self._working: MemoryDatabase | None = None

    def __enter__(self) -> Self:
        self._begin()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # commit されないまま抜けたら、作業用コピーごと捨てる = rollback。
        self._working = None

    def commit(self) -> None:
        if self._working is None:
            raise RuntimeError("commit() called outside of a transaction")
        self._db.replace_with(self._working)
        # commit 後も同じトランザクション内で読み書きを続けられるよう、作業用コピーを
        # 作り直す。本物の DB で commit 後に同じセッションを使い続けるのと同じ状況。
        self._begin()

    def rollback(self) -> None:
        self._begin()

    def _begin(self) -> None:
        self._working = deepcopy(self._db)
        self.plans = InMemoryPlanRepository(self._working)
        self.subscriptions = InMemorySubscriptionRepository(self._working, self._db)
        self.invoices = InMemoryInvoiceRepository(self._working, self._db)

"""インメモリの UnitOfWork。"""

from __future__ import annotations

from types import TracebackType
from typing import Self

from billing.domain.repositories import (
    InvoiceRepository,
    PlanRepository,
    SubscriptionRepository,
)
from billing.infrastructure.persistence.memory.database import MemoryDatabase, Staging
from billing.infrastructure.persistence.memory.repositories import (
    InMemoryInvoiceRepository,
    InMemoryPlanRepository,
    InMemorySubscriptionRepository,
)


class InMemoryUnitOfWork:
    """変更をいったん溜め、commit のときだけデータベースに反映する。

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
        self._staging: Staging | None = None

    def __enter__(self) -> Self:
        self._begin()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # commit されないまま抜けたら、溜めた変更ごと捨てる = rollback。
        self._staging = None

    def commit(self) -> None:
        if self._staging is None:
            raise RuntimeError("commit() called outside of a transaction")
        # 変更した集約だけを反映する。データベース全体を置き換えると、自分が触って
        # いない集約への他トランザクションの更新まで巻き戻してしまう。楽観ロックは
        # 触った集約のバージョンしか見ないので、その巻き戻しは誰にも検出されない。
        self._db.plans.update(self._staging.plans)
        self._db.subscriptions.update(self._staging.subscriptions)
        self._db.invoices.update(self._staging.invoices)
        self._db.versions.update(self._staging.versions)
        # commit 後も同じ UnitOfWork を使い続けられるよう作り直す。バージョン追跡の
        # 前提が変わるため、リポジトリごと入れ替える。
        self._begin()

    def rollback(self) -> None:
        self._begin()

    def _begin(self) -> None:
        self._staging = Staging()
        self.plans = InMemoryPlanRepository(self._db, self._staging)
        self.subscriptions = InMemorySubscriptionRepository(self._db, self._staging)
        self.invoices = InMemoryInvoiceRepository(self._db, self._staging)

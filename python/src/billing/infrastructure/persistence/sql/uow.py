"""SQLAlchemy の UnitOfWork。"""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.pool import StaticPool

from billing.domain.repositories import (
    InvoiceRepository,
    PlanRepository,
    SubscriptionRepository,
)
from billing.infrastructure.persistence.sql.repositories import (
    SqlInvoiceRepository,
    SqlPlanRepository,
    SqlSubscriptionRepository,
)


def create_sqlite_engine(url: str = "sqlite:///:memory:") -> Engine:
    """SQLite 用のエンジンを作る。

    ``:memory:`` は接続ごとに別の DB になるため、StaticPool で 1 本の接続を使い回す。
    外部キー制約は SQLite では既定で無効なので、明示的に有効化する。ここを忘れると
    「参照先のないデータが平然と入る DB」で結合テストを書くことになる。
    """
    connect_args = {"check_same_thread": False} if ":memory:" in url else {}
    engine = create_engine(
        url,
        poolclass=StaticPool if ":memory:" in url else None,
        connect_args=connect_args,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


class SqlAlchemyUnitOfWork:
    """1 つの接続と 1 つのトランザクションを、3 つのリポジトリで共有する。

    リポジトリごとに勝手に接続を張ると、同じユースケースの中の書き込みが別々の
    トランザクションに散らばる。「請求書は作られたが契約の状態は元のまま」という
    半端な状態が本番で生まれるのは、たいていこれが原因である。
    """

    # 具象クラスではなく抽象型で宣言する。UnitOfWork を使う側に実装クラスの名前が
    # 見えないようにするための注釈であり、同時に UnitOfWork プロトコルを満たす条件でもある。
    subscriptions: SubscriptionRepository
    invoices: InvoiceRepository
    plans: PlanRepository

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._connection: Connection | None = None

    def __enter__(self) -> Self:
        self._connection = self._engine.connect()
        self._connection.begin()
        self._bind(self._connection)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._connection is None:
            return
        # commit されていなければ、正常終了であっても巻き戻す。
        if self._connection.in_transaction():
            self._connection.rollback()
        self._connection.close()
        self._connection = None

    def commit(self) -> None:
        connection = self._require_connection()
        connection.commit()
        connection.begin()
        # commit でバージョン追跡の前提が変わるため、リポジトリを作り直す。
        self._bind(connection)

    def rollback(self) -> None:
        connection = self._require_connection()
        connection.rollback()
        connection.begin()
        self._bind(connection)

    def _require_connection(self) -> Connection:
        if self._connection is None:
            raise RuntimeError("UnitOfWork used outside of a with-block")
        return self._connection

    def _bind(self, connection: Connection) -> None:
        self.plans = SqlPlanRepository(connection)
        self.subscriptions = SqlSubscriptionRepository(connection)
        self.invoices = SqlInvoiceRepository(connection)

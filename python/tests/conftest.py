"""3 つの永続化実装を同じテストで回すための共通フィクスチャ。

``uow_factory`` は memory / sqlite / dynamodb の 3 通りにパラメータ化されている。
このフィクスチャを使うテストは、書いた覚えのないまま 3 回実行される。ユースケースの
テストがそのまま「実装を差し替えても壊れないことの証明」になる。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from billing.application.charging import UnitOfWorkFactory
from billing.infrastructure.clock import FixedClock
from billing.infrastructure.ids import SequentialIdGenerator
from billing.infrastructure.payment.fake_gateway import FakePaymentGateway
from billing.presentation.container import Container

#: すべてのテストの基準時刻。実時間に依存させない。
NOW = datetime(2026, 1, 1, tzinfo=UTC)

DYNAMO_TABLE = "billing-test"
DYNAMO_REGION = "ap-northeast-1"


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def ids() -> SequentialIdGenerator:
    return SequentialIdGenerator("id")


@pytest.fixture
def gateway() -> FakePaymentGateway:
    return FakePaymentGateway()


@pytest.fixture(params=["memory", "sqlite", "dynamodb"])
def persistence(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def uow_factory(
    persistence: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[UnitOfWorkFactory]:
    match persistence:
        case "memory":
            from billing.infrastructure.persistence.memory import (
                InMemoryUnitOfWork,
                MemoryDatabase,
            )

            database = MemoryDatabase()
            yield lambda: InMemoryUnitOfWork(database)

        case "sqlite":
            from billing.infrastructure.persistence.sql import create_schema
            from billing.infrastructure.persistence.sql.uow import (
                SqlAlchemyUnitOfWork,
                create_sqlite_engine,
            )

            # :memory: ではなくファイルを使う。StaticPool で 1 本の接続を共有すると、
            # 「2 つのトランザクションが同時に走る」状況を再現できないため。
            engine = create_sqlite_engine(f"sqlite:///{tmp_path}/billing.db")
            create_schema(engine)
            try:
                yield lambda: SqlAlchemyUnitOfWork(engine)
            finally:
                engine.dispose()

        case "dynamodb":
            from moto import mock_aws

            from billing.infrastructure.persistence.dynamo import (
                DynamoUnitOfWork,
                create_dynamo_client,
                create_table,
            )

            # moto に本物の認証情報を拾わせない。
            for name in (
                "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY",
                "AWS_SECURITY_TOKEN",
                "AWS_SESSION_TOKEN",
            ):
                monkeypatch.setenv(name, "testing")
            monkeypatch.setenv("AWS_DEFAULT_REGION", DYNAMO_REGION)

            with mock_aws():
                client = create_dynamo_client(region_name=DYNAMO_REGION)
                create_table(client, DYNAMO_TABLE)
                yield lambda: DynamoUnitOfWork(client, DYNAMO_TABLE)

        case unknown:  # pragma: no cover - パラメータの追加漏れを知らせる
            raise AssertionError(f"unknown persistence backend: {unknown}")


@pytest.fixture
def container(
    uow_factory: UnitOfWorkFactory,
    clock: FixedClock,
    ids: SequentialIdGenerator,
    gateway: FakePaymentGateway,
) -> Container:
    """本番と同じ配線で、外界（時計・決済代行）だけを差し替えたコンテナ。

    ユースケースを 1 つずつ手で組み立てないのは、テストが「配線が正しいこと」も
    同時に検証するようにするため。合成ルートを迂回してテストを書くと、
    本番でだけ配線を間違えていても誰も気づかない。
    """
    built = Container(uow_factory=uow_factory, clock=clock, ids=ids, gateway=gateway)
    built.seed_plans()
    return built

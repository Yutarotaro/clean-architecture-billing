"""実行時の設定。

どの永続化実装を使うかは設定 1 つで決まる。コードを 1 行も変えずに
インメモリ・SQLite・DynamoDB を差し替えられることが、レイヤー分割が機能している
何よりの証拠になる。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class PersistenceKind(StrEnum):
    MEMORY = "memory"
    SQLITE = "sqlite"
    DYNAMODB = "dynamodb"


@dataclass(frozen=True, slots=True)
class Settings:
    persistence: PersistenceKind = PersistenceKind.MEMORY
    database_url: str = "sqlite:///./billing.db"
    dynamo_table: str = "billing"
    dynamo_endpoint_url: str | None = None
    aws_region: str = "ap-northeast-1"
    seed_plans: bool = True

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        return cls(
            persistence=PersistenceKind(source.get("BILLING_PERSISTENCE", "memory")),
            database_url=source.get("BILLING_DATABASE_URL", "sqlite:///./billing.db"),
            dynamo_table=source.get("BILLING_DYNAMO_TABLE", "billing"),
            dynamo_endpoint_url=source.get("BILLING_DYNAMO_ENDPOINT"),
            aws_region=source.get("AWS_REGION", "ap-northeast-1"),
            seed_plans=source.get("BILLING_SEED_PLANS", "1") != "0",
        )

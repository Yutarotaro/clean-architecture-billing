"""テーブル定義。

ここが「ドメインモデルとテーブルは別物である」ことが最も見える場所である。
``Money`` は 1 つの値オブジェクトだが、テーブルでは amount と currency の 2 列になる。
``BillingPeriod`` も start と end の 2 列に分かれる。この対応づけを引き受けるのが
インフラ層の仕事で、その代わりドメインは正規化やインデックスの都合から自由でいられる。
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Engine,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
)

METADATA = MetaData()

#: 時刻は ISO 8601（UTC、マイクロ秒まで固定長）の文字列で保存する。
#: SQLite の DATETIME 型はタイムゾーンを保持できず、読み戻すと naive になる。
#: 固定長にしているのは、文字列の辞書順と時刻の順序を一致させるため。
TIMESTAMP = String(32)

plans = Table(
    "plans",
    METADATA,
    Column("id", String(64), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("price_amount", Integer, nullable=False),
    Column("price_currency", String(3), nullable=False),
    Column("interval", String(16), nullable=False),
)

subscriptions = Table(
    "subscriptions",
    METADATA,
    Column("id", String(64), primary_key=True),
    Column("customer_id", String(64), nullable=False, index=True),
    Column("plan_id", String(64), ForeignKey("plans.id"), nullable=False),
    Column("status", String(16), nullable=False),
    Column("period_start", TIMESTAMP, nullable=False),
    Column("period_end", TIMESTAMP, nullable=False),
    Column("cancel_at_period_end", Boolean, nullable=False, default=False),
    Column("past_due_since", TIMESTAMP, nullable=True),
    Column("canceled_at", TIMESTAMP, nullable=True),
    Column("trial_end", TIMESTAMP, nullable=True),
    # 楽観ロック用。ドメインオブジェクトには持たせず、リポジトリだけが管理する。
    Column("version", Integer, nullable=False, default=1),
    # 更新バッチが毎回フルスキャンしないための索引。
    Index("ix_subscriptions_due", "status", "period_end"),
)

invoices = Table(
    "invoices",
    METADATA,
    Column("id", String(64), primary_key=True),
    Column("customer_id", String(64), nullable=False, index=True),
    Column("subscription_id", String(64), ForeignKey("subscriptions.id"), nullable=False),
    Column("status", String(16), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("issued_at", TIMESTAMP, nullable=True),
    Column("paid_at", TIMESTAMP, nullable=True),
    # 冪等性はアプリケーションの if 文ではなく、まず DB の一意制約で守る。
    # 二重発行を防ぐ最後の砦がここにあると、競合状態が起きても壊れ方が静かにならない。
    Column("idempotency_key", String(200), nullable=True, unique=True),
    Column("version", Integer, nullable=False, default=1),
)

invoice_lines = Table(
    "invoice_lines",
    METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("invoice_id", String(64), ForeignKey("invoices.id"), nullable=False, index=True),
    Column("position", Integer, nullable=False),
    Column("description", String(500), nullable=False),
    Column("amount", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
)


def create_schema(engine: Engine) -> None:
    """テーブルを作る。

    本番でこれを呼ぶのではなく、マイグレーションツール（Alembic 等）に置き換える
    ことを想定している。サンプルの実行と結合テストを 1 コマンドで始められるように
    用意してある。
    """
    METADATA.create_all(engine)

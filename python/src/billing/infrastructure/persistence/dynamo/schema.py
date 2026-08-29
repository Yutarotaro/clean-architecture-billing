"""シングルテーブル設計のキー構造。

RDB のように「1 エンティティ 1 テーブル」にせず、全種類の項目を 1 つのテーブルに
入れる。DynamoDB にはテーブルをまたぐ結合も、テーブルをまたぐトランザクションの
安価な手段もないため、まとめておいた方が扱いやすいという事情による。

この歪さがドメイン層に漏れていないことが重要である。``PK`` や ``GSI1PK`` という
語はこのパッケージの外に一度も出てこない。
"""

from __future__ import annotations

from typing import Any

from billing.domain.ids import CustomerId, InvoiceId, PlanId, SubscriptionId

#: 更新バッチが引く索引。解約済みの契約はこの属性を持たないため索引に載らない
#: （sparse index）。「対象外のものは索引に入れない」ことで、走査量を状態ではなく
#: 実際の件数に比例させる。
GSI1 = "gsi1"
GSI2 = "gsi2"

LIVE_PARTITION = "LIVE"
PAST_DUE_PARTITION = "PASTDUE"


def plan_key(plan_id: PlanId | str) -> dict[str, str]:
    return {"pk": f"PLAN#{plan_id}", "sk": "META"}


def subscription_key(subscription_id: SubscriptionId | str) -> dict[str, str]:
    return {"pk": f"SUB#{subscription_id}", "sk": "META"}


def invoice_key(invoice_id: InvoiceId | str) -> dict[str, str]:
    return {"pk": f"INV#{invoice_id}", "sk": "META"}


def idempotency_key(key: str) -> dict[str, str]:
    """冪等キーそのものを 1 つの項目にする。

    DynamoDB には UNIQUE 制約がない。代わりに「その鍵の項目がまだ存在しないこと」を
    条件に書き込む。請求書本体と同じトランザクションに入れれば、二重発行は
    アプリケーションの if 文ではなくデータベースの側で防がれる。
    """
    return {"pk": f"IDEM#{key}", "sk": "META"}


def customer_invoices_partition(customer_id: CustomerId | str) -> str:
    return f"CUST#{customer_id}"


def create_table(client: Any, table_name: str) -> None:
    """テーブルと GSI を作る。本番では IaC（CDK/Terraform）に置き換える想定。"""
    client.create_table(
        TableName=table_name,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "gsi1pk", "AttributeType": "S"},
            {"AttributeName": "gsi1sk", "AttributeType": "S"},
            {"AttributeName": "gsi2pk", "AttributeType": "S"},
            {"AttributeName": "gsi2sk", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                # 生存中の契約を、請求期間の終わり順に並べる。更新バッチはここを
                # 「期限が来たものだけ」前から読む。
                "IndexName": GSI1,
                "KeySchema": [
                    {"AttributeName": "gsi1pk", "KeyType": "HASH"},
                    {"AttributeName": "gsi1sk", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                # 2 つの用途で共有している。パーティションキーの接頭辞
                # （PASTDUE / CUST#）で名前空間が分かれるので衝突しない。
                # GSI はテーブルあたりの本数に上限があるため、こうして相乗りさせるのが
                # シングルテーブル設計の常套手段である。
                "IndexName": GSI2,
                "KeySchema": [
                    {"AttributeName": "gsi2pk", "KeyType": "HASH"},
                    {"AttributeName": "gsi2sk", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
    )
    client.get_waiter("table_exists").wait(TableName=table_name)

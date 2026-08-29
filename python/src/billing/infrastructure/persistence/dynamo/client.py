"""DynamoDB クライアントの生成。

合成ルートが ``import boto3`` と書けば同じことはできるが、そうすると
presentation 層に AWS SDK への依存が生まれる。「どの実装を選ぶか」を決めるのは
合成ルートの仕事でよいが、「その実装をどう作るか」はインフラ層の中に留める。

この分離は、tests/unit/test_layer_dependencies.py が実際に落ちて気づいたもの。
"""

from __future__ import annotations

from typing import Any

import boto3


def create_dynamo_client(*, region_name: str, endpoint_url: str | None = None) -> Any:
    """DynamoDB のクライアントを作る。

    ``endpoint_url`` を指定すると DynamoDB Local や LocalStack に向けられる。
    テストでは moto がこの呼び出しごと差し替える。
    """
    return boto3.client("dynamodb", region_name=region_name, endpoint_url=endpoint_url)

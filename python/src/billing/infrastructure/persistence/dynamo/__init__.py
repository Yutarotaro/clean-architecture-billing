"""DynamoDB を使った永続化実装。

この実装が存在する理由は、実際に DynamoDB で運用したいからというより、
「リポジトリという抽象が、SQL とはデータモデルも整合性の保証も違う相手に対しても
成立するのか」を確かめるためである。答えは docs/persistence-portability.md に書いた。
"""

from billing.infrastructure.persistence.dynamo.client import create_dynamo_client
from billing.infrastructure.persistence.dynamo.schema import create_table
from billing.infrastructure.persistence.dynamo.uow import DynamoUnitOfWork

__all__ = ["create_dynamo_client", "create_table", "DynamoUnitOfWork"]

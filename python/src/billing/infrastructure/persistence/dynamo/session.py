"""DynamoDB への読み書きを 1 つのトランザクション境界にまとめる。

DynamoDB には「トランザクションを開く」という操作がない。あるのは
``TransactWriteItems``、すなわち「複数の書き込みを 1 発でまとめて送る」API だけである。
読み取りはその外側で行われる。

そこで、このセッションは書き込みをいったん手元に溜め、``commit`` で一括送信する。
溜めた変更は ``overlay`` にも入れておき、同じセッション内の ``get`` から見えるように
する（read-your-writes）。SQL のトランザクションに近い使い勝手を、上の層に対して
再現するための仕掛けである。

再現できないものもある。詳細は docs/persistence-portability.md を参照。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from billing.application.errors import ConcurrencyConflict
from billing.infrastructure.persistence.errors import DuplicateEntity

#: TransactWriteItems 1 回で送れる項目数の上限。
#: この数字がユースケースの ``limit`` と無関係でいられないことが、抽象の漏れである。
TRANSACTION_ITEM_LIMIT = 100


@dataclass
class _StagedWrite:
    key: tuple[str, str]
    item: dict[str, Any]
    condition: str | None
    expression_values: dict[str, Any]
    entity: str
    entity_id: str
    duplicate_message: str | None = None

    def as_conflict(self) -> Exception:
        if self.duplicate_message is not None:
            return DuplicateEntity(self.duplicate_message)
        return ConcurrencyConflict(self.entity, self.entity_id)


class DynamoSession:
    def __init__(self, client: Any, table_name: str) -> None:
        self._client = client
        self._table = table_name
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()
        self._staged: dict[tuple[str, str], _StagedWrite] = {}
        self._overlay: dict[tuple[str, str], dict[str, Any]] = {}

    # ------------------------------------------------------------------ 読み取り

    def get(self, key: dict[str, str]) -> dict[str, Any] | None:
        cache_key = (key["pk"], key["sk"])
        staged = self._overlay.get(cache_key)
        if staged is not None:
            return staged
        response = self._client.get_item(
            TableName=self._table,
            Key=self._serialize(key),
            ConsistentRead=True,
        )
        item = response.get("Item")
        return None if item is None else self._deserialize(item)

    def query(
        self,
        *,
        index: str,
        key_condition: str,
        values: dict[str, Any],
        limit: int,
        names: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """GSI を引く。

        未 commit の変更はここに反映されない。SQL なら同じトランザクション内の
        INSERT が後続の SELECT から見えるが、DynamoDB の索引は書き込みが確定して
        初めて更新される（しかも結果整合）。ユースケースが「書いた直後にクエリで
        拾い直す」形になっていると、ここで壊れる。
        """
        request: dict[str, Any] = {
            "TableName": self._table,
            "IndexName": index,
            "KeyConditionExpression": key_condition,
            "ExpressionAttributeValues": self._serialize(values),
            "Limit": limit,
        }
        if names:
            request["ExpressionAttributeNames"] = names
        response = self._client.query(**request)
        return [self._deserialize(item) for item in response.get("Items", [])]

    def scan(
        self,
        *,
        filter_expression: str,
        values: dict[str, Any],
        names: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """テーブル全体を走査する。

        件数が増えれば必ず遅くなる操作なので、使ってよい場所を限定する。ここでは
        プラン一覧（数十件で頭打ちになるマスタデータ）にだけ使っている。契約や
        請求書に対して Scan を書き始めたら、それはキー設計を見直す合図である。
        """
        request: dict[str, Any] = {
            "TableName": self._table,
            "FilterExpression": filter_expression,
            "ExpressionAttributeValues": self._serialize(values),
        }
        if names:
            request["ExpressionAttributeNames"] = names
        response = self._client.scan(**request)
        return [self._deserialize(item) for item in response.get("Items", [])]

    # ------------------------------------------------------------------ 書き込み

    def stage(
        self,
        *,
        key: dict[str, str],
        item: dict[str, Any],
        condition: str | None,
        values: dict[str, Any],
        entity: str,
        entity_id: str,
        duplicate_message: str | None = None,
    ) -> None:
        """書き込みを 1 件溜める。

        同じ項目に対する 2 回目以降の書き込みは、内容だけを差し替えて 1 件にまとめる。
        TransactWriteItems は同じキーの項目を 2 つ含むことを許さないため、ここで
        畳んでおかないと「1 つのユースケースで同じ集約を 2 回 save した」だけで
        実行時エラーになる。
        """
        cache_key = (key["pk"], key["sk"])
        existing = self._staged.get(cache_key)
        if existing is not None:
            # 条件は最初の書き込みのものを使う。途中の版を条件にしても、その版は
            # まだデータベースに存在しない。
            merged = dict(item)
            merged["version"] = existing.item["version"]
            self._staged[cache_key] = _StagedWrite(
                key=cache_key,
                item=merged,
                condition=existing.condition,
                expression_values=existing.expression_values,
                entity=existing.entity,
                entity_id=existing.entity_id,
                duplicate_message=existing.duplicate_message,
            )
            self._overlay[cache_key] = merged
            return

        self._staged[cache_key] = _StagedWrite(
            key=cache_key,
            item=item,
            condition=condition,
            expression_values=values,
            entity=entity,
            entity_id=entity_id,
            duplicate_message=duplicate_message,
        )
        self._overlay[cache_key] = item

    def commit(self) -> None:
        if not self._staged:
            self._overlay.clear()
            return

        staged = list(self._staged.values())
        if len(staged) > TRANSACTION_ITEM_LIMIT:
            raise RuntimeError(
                f"this unit of work touches {len(staged)} items, but DynamoDB "
                f"transactions are limited to {TRANSACTION_ITEM_LIMIT}"
            )

        try:
            self._client.transact_write_items(
                TransactItems=[self._as_transact_item(write) for write in staged]
            )
        except ClientError as exc:
            self._translate(exc, staged)
            raise

        self._staged.clear()
        self._overlay.clear()

    def rollback(self) -> None:
        # 何も送っていないので、溜めたものを捨てるだけでよい。
        self._staged.clear()
        self._overlay.clear()

    # ------------------------------------------------------------------ 内部

    def _as_transact_item(self, write: _StagedWrite) -> dict[str, Any]:
        put: dict[str, Any] = {"TableName": self._table, "Item": self._serialize(write.item)}
        if write.condition is not None:
            put["ConditionExpression"] = write.condition
            if write.expression_values:
                put["ExpressionAttributeValues"] = self._serialize(write.expression_values)
        return {"Put": put}

    def _translate(self, exc: ClientError, staged: list[_StagedWrite]) -> None:
        """DynamoDB 固有の失敗を、上の層が知っている言葉に翻訳する。

        ``TransactionCanceledException`` や ``ConditionalCheckFailed`` という語が
        このパッケージの外に出ていかないようにするのが、境界の実装の責任である。
        """
        if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
            return
        reasons = exc.response.get("CancellationReasons", [])
        for write, reason in zip(staged, reasons, strict=False):
            if reason.get("Code") == "ConditionalCheckFailed":
                raise write.as_conflict() from exc

    def _serialize(self, value: dict[str, Any]) -> dict[str, Any]:
        return {key: self._serializer.serialize(item) for key, item in value.items()}

    def _deserialize(self, item: dict[str, Any]) -> dict[str, Any]:
        return {key: self._deserializer.deserialize(value) for key, value in item.items()}

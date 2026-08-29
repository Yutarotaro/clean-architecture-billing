# ADR-0004: 楽観ロックのバージョンをリポジトリに閉じ込める

**状態**: 採用

## 文脈

複数の実行が同じ契約を同時に更新すると、後から書いたほうが先の変更を消します（lost update）。
SQL なら `SELECT ... FOR UPDATE` で悲観ロックが使えますが、
**DynamoDB には悲観ロックが存在しません**。選べるのは楽観ロックだけです。

3 実装で意味論を揃えるには、全部を楽観ロックにする必要がありました。
問題は、バージョン番号をどこに持たせるかです。

## 決定

**ドメインオブジェクトには持たせません。** リポジトリが
「この UnitOfWork の中で、この ID をバージョン幾つで読んだか」を記録します。

```python
class VersionTracker:
    def remember(self, entity_id: str, version: int) -> None: ...
    def expected(self, entity_id: str) -> int: ...
```

```python
def get(self, subscription_id):
    row = ...
    self._versions.remember(row.id, row.version)     # 読んだときに覚える
    return mappers.row_to_subscription(row)

def save(self, subscription):
    expected = self._versions.expected(entity_id)    # 書くときに使う
    result = conn.execute(update().where(version == expected).values(version=expected + 1))
    if result.rowcount != 1:
        raise ConcurrencyConflict("subscription", entity_id)
```

## 理由

`Subscription` に `version: int` を生やすのが一般的な実装です（Hibernate の `@Version` など）。
それを避けたのは、**楽観ロックが永続化の都合であって、
契約というビジネス概念の一部ではない**からです。

`version` フィールドがあると、ドメインのテストでそれを気にすることになります。

```python
# こう書きたくない
assert subscription == Subscription(id="sub-1", ..., version=3)
```

またインメモリ実装では本来 version が不要ですが、
ドメインに持たせると全実装が管理を強いられます。

**副次的な効果**として、「読まずに `save` する」を検出できるようになりました。
バージョンを覚えていない ID を更新しようとするのは、
誰かの変更を無条件に上書きすることに等しいので、その時点で弾いています。

```python
def expected(self, entity_id: str) -> int:
    version = self._versions.get(entity_id)
    if version is None:
        raise ConcurrencyConflict(self._entity, entity_id)
    return version
```

## 結果

**良かったこと**: ドメイン層は楽観ロックの存在を最後まで知りません。
3 実装が同じ契約テストを通ります。

**引き受けたこと**: リポジトリが UnitOfWork ごとに状態を持つようになりました。
`commit` するとバージョン追跡の前提が変わるので、リポジトリを作り直しています。

**見つかった漏れ**: 衝突が判明するタイミングが実装ごとに違いました。

| 実装 | 判明する場所 |
|---|---|
| memory / SQLite | `save()` |
| DynamoDB | `commit()`（`TransactWriteItems` を送るまで分からない） |

契約としては「`save` または `commit` のどちらかで送出される」と定めています。
[抽象の漏れ](../persistence-portability.md#4-衝突がいつ分かるか) として記録しました。

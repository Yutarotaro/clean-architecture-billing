# テスト戦略

レイヤーを分けた見返りは、**それぞれの層を別々にテストできること**です。
この章では、どの層に何のテストを置いたかを整理します。

## 全体像

| 種類 | 場所 | 何を確かめるか | 速さ |
|---|---|---|---|
| 依存の向き | `tests/unit/test_layer_dependencies.py`<br/>`internal/arch/layers_test.go` | import が内側だけを向いているか | 即座 |
| ドメイン単体 | `tests/unit/`<br/>`internal/domain/*_test.go` | ビジネスルール | ミリ秒 |
| 契約テスト | `tests/contract/`<br/>`internal/billingtest/contract*_test.go` | 実装が同じ振る舞いをするか | 秒 |
| ユースケース | `tests/usecase/`<br/>`internal/billingtest/usecase_test.go` | 手順・トランザクション境界・冪等性 | 秒 |
| HTTP | `tests/api/`<br/>`internal/billingtest/http_test.go` | ステータスコードへの翻訳、必須ヘッダ | 秒 |

Python 262 ケース、Go 224 ケース。すべて CI で実行されます。

## ドメイン層: 表を書くだけでテストになる

外部依存が一切ないので、日割りのような「ビジネス上の争点になりやすい計算」を
パラメータ表として書けます。

```python
@pytest.mark.parametrize(
    ("amount", "ratio", "expected"),
    [
        (3_000, Fraction(1, 2), 1_500),
        (3_000, Fraction(1, 3), 1_000),
        (1_000, Fraction(1, 3), 333),   # 切り捨て
        (1_000, Fraction(2, 3), 666),
        (999, Fraction(1, 2), 499),
    ],
)
def test_scale_truncates_towards_zero(amount, ratio, expected):
    assert Money(amount).scale(ratio) == Money(expected)
```

これができるのは、`Money` が DB もフレームワークも知らないからです。

## 時刻を注入する

「支払いに失敗してから 14 日で解約される」を検証するのに、14 日待つわけにはいきません。
`Clock` をポートにしてあるので、時計を進めるだけで再現できます。

```python
clock.set(FEB)
report = container.renew_due_subscriptions.execute()
assert report.payment_failed == 1

clock.set(datetime(2026, 2, 10, tzinfo=UTC))     # 猶予期間の途中
assert container.renew_due_subscriptions.execute().canceled_for_nonpayment == 0

clock.set(datetime(2026, 2, 16, tzinfo=UTC))     # 14 日超過
assert container.renew_due_subscriptions.execute().canceled_for_nonpayment == 1
```

## 契約テスト: 1 つ書けば実装の数だけ走る

`uow_factory` フィクスチャがパラメータ化されているので、
**このフィクスチャを使うテストは、書いた覚えのないまま 3 回実行されます**。

```python
@pytest.fixture(params=["memory", "sqlite", "dynamodb"])
def persistence(request): ...
```

Go でも同じことを `eachBackend` でやっています。

```go
func eachBackend(t *testing.T, fn func(t *testing.T, factory usecase.UnitOfWorkFactory)) {
    for _, b := range backends(t) {
        t.Run(b.name, func(t *testing.T) { fn(t, b.factory) })
    }
}
```

新しい永続化実装を足したくなったら、まずこのリストに追加して緑にすればよい。
逆に、ここに書けない振る舞い（実装ごとに違ってしまうもの）を見つけたら、
それは抽象が漏れている箇所です（[漏れた 4 点](persistence-portability.md#漏れたもの)）。

## モックではなく fake を使う

決済代行は `FakePaymentGateway` として実装しています。
**呼ばれた回数を検証するのではなく、本物と同じ規約を実際に実装**しています。

```python
cached = self._results.get(idempotency_key)
if cached is not None:
    # 本物の決済代行と同じ振る舞い。同じ鍵で二度目が来ても、
    # 新しく課金せずに一度目の結果をそのまま返す。
    return cached
```

この違いは重要です。モックだと「二重課金しないこと」をテストしようとしても、
**モック自身が二重課金を許してしまう**ので、何も検証できません。

fake が規約を守っているおかげで、こう書けます。

```python
first = container.change_plan.execute(command)
second = container.change_plan.execute(command)   # 同じ冪等キーで再送

assert first.invoice.id == second.invoice.id
assert gateway.settled_amount == 2_000            # 二重課金されていない
```

同じ理由で、インメモリのリポジトリも**トランザクションの意味論を守っています**。
「テスト用だから commit はなくていい」とすると、commit を書き忘れたユースケースが
テストでは通って本番で壊れます。

## テストが設計を直した例

!!! example "1. 合成ルートが boto3 を import していた"
    依存の向きのテストが検出。DynamoDB クライアントの生成をインフラ層に移しました。

!!! example "2. インメモリ実装が競合を検出できなかった"
    契約テストの `test_concurrent_update_is_detected` が memory でだけ落ちました。
    スナップショットの中だけを見ていたのが原因で、
    バージョン比較の基準をコミット済みの実体に変えました。

!!! example "3. 回帰テストが 5 つ目の実装差を掘り当てた"
    「触っていない集約への更新が消えないこと」を検証するテストを足したところ、
    Go の SQLite（WAL）だけが落ちました。読んだ時点のスナップショットが古いと、
    触っていない集約が動いただけでも書き込みが拒まれます。
    契約を「更新が失われないこと」に狭める判断につながりました。

!!! example "4. mypy が Protocol の不変性を指摘した"
    `UnitOfWork` の属性を可変で宣言していたため、具象リポジトリを持つ実装が
    Protocol を満たせませんでした。読み取り専用プロパティにして共変にしています。

    ```python
    class UnitOfWork(Protocol):
        @property
        def subscriptions(self) -> SubscriptionRepository: ...
    ```

## レビューで見つかった欠陥

ここまでのテストをすべて緑にしたうえで、コードレビューを 1 回通しました。
**緑のまま壊れている箇所が 8 件**見つかりました。テストが「実装をなぞっただけ」に
なっていると何も守れない、という実例なので、内訳を残しておきます。

| 欠陥 | テストの側の問題 |
|---|---|
| 順序の入れ替わった失敗 webhook で、支払い済みの顧客が解約される | **テストが誤った挙動を仕様として固定していた**。支払い済みの請求書に失敗通知を投げて `past_due` を期待していた |
| 決済が例外を投げるとバッチが止まり、請求書が永久に取り残される | 決済代行が例外を投げる経路を一度もテストしていなかった |
| ダウングレードと試用中の変更で再送が冪等でない | 「ダウングレード」と「再送」のテストが別々にあり、**その交差が一度もなかった** |
| インメモリ実装が他トランザクションの commit を巻き戻す | 競合のテストが**同一の集約しか**競合させていなかった |
| 冪等キーが内部の鍵と衝突すると 500 / panic | クライアントが任意の文字列を送れることを考慮していなかった |
| `remaining_ratio` が float を経由していた | docstring が「float を経由しない」と書いているのに、それを確かめるテストがなかった |
| fake が冪等キーの引数を照合しない | **冪等性を検証するための fake が、冪等性の誤用を隠していた** |
| 丸めの向きと時刻表現が言語間で割れていた | 両言語の実装を突き合わせるテストがなかった |

いずれも回帰テストを追加しました。特に上から 3 つは、
**テストの分け方そのものが穴を作っていた**ケースです。
「A のテスト」と「B のテスト」を別々に書くと、A かつ B が抜けます。

さらに、その回帰テストを書いたことで
[5 つ目の実装差](persistence-portability.md#4-衝突がいつ分かるか)も見つかりました。
WAL モードの SQLite は、触っていない集約が動いただけでも書き込みを拒みます。

## 何をテストしていないか

- **決済代行の本物の実装**。ポートは切ってありますが、Stripe 等の adapter は含みません。
- **並行実行そのもの**。楽観ロックの衝突は逐次的に再現しています。
  実際のレースコンディションの検証には、別の道具（`go test -race` や負荷試験）が要ります。
- **DynamoDB の本番環境**。テストは `moto` で行っています。
  スロットリングや結果整合の遅延といった、本番でしか起きないことは対象外です。

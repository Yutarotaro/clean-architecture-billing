# ADR-0003: ORM にドメインモデルをマップさせず、変換を手で書く

**状態**: 採用

## 文脈

ドメインの `Subscription` を SQLite の行に変換する必要があります。選択肢は 3 つでした。

1. **ドメインクラスを ORM のモデルにする**（SQLAlchemy の declarative、Django の Model）
2. **imperative mapping**（SQLAlchemy の `registry.map_imperatively`）でドメインクラスを外からマップする
3. **別の行モデルを定義し、変換を手で書く**（データマッパーを手書き）

## 決定

**3 を選びました。** `infrastructure/persistence/sql/mappers.py` と
`internal/infra/sqlite/mappers.go` に、退屈な変換コードが並んでいます。

```python
def subscription_to_row(subscription: Subscription) -> dict[str, Any]:
    return {
        "id": str(subscription.id),
        ...
        "period_start": to_iso(subscription.current_period.start),
        "period_end": to_iso(subscription.current_period.end),
    }
```

## 理由

**1 を退けた理由**: ドメインクラスが ORM の基底クラスを継承すると、
`domain` パッケージが SQLAlchemy に依存します。
[ADR-0001](0001-layer-boundaries.md) の依存検査がそのまま落ちます。
さらに属性アクセスが遅延ロードに化けるため、
「ビジネスルールのテストのつもりが DB アクセスしていた」が起こります。

**2 を退けた理由**: 依存の向きは守れますが、`BillingPeriod` や `Money` のような
値オブジェクトを composite でマップする設定が、行を手で書くより複雑になりました。
また DynamoDB 版では結局マッパーを手書きするので、**SQL だけ別の仕組みにする理由が薄い**。

**3 を選んだ理由**: `mappers.py` が存在すること自体が、
「ドメインモデルとテーブルは別物である」という主張になります。
`Money` は 1 つの値オブジェクトですが、テーブルでは `amount` と `currency` の 2 列になる。
`BillingPeriod` も `period_start` と `period_end` に分かれる。
この対応づけを引き受けるのがインフラ層の仕事で、
その代わりドメインは正規化やインデックスの都合から自由でいられます。

## 結果

**良かったこと**: DynamoDB 実装を足すときに、ドメイン層を 1 行も触りませんでした。
請求明細は SQL では別テーブルへの外部キー、DynamoDB では項目に埋め込んだリストですが、
`domain.Invoice` はどちらも知りません。

**引き受けたこと**: 列を 1 つ足すたびに、変換コードの 2 箇所（往路と復路）を直します。
テーブルが 4 つ、実装が 3 つある現状では許容範囲ですが、
テーブルが 30 に増えたら別の判断が要ります。

**副産物**: 変換を自分で書いているので、時刻の表現を明示的に決められました。
固定長の ISO 8601 文字列にすることで、文字列の辞書順と時刻の順序が一致し、
`WHERE period_end <= ?` と DynamoDB のソートキーの両方がそのまま使えます。

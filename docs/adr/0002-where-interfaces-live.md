# ADR-0002: インターフェースの置き場所を言語の慣習に合わせる

**状態**: 採用

## 文脈

リポジトリのインターフェースをどこに置くかには、2 つの流儀があります。

1. **ドメイン層に置く**（DDD 流）: 集約単位のリポジトリはドメインの語彙の一部である
2. **利用側に置く**（Go 流）: インターフェースは要求する側が定義する

Python と Go で同じ構造を作るにあたり、どちらかに揃えるか、言語ごとに変えるかを決める必要がありました。

## 決定

**言語の慣習に合わせて変えます。**

| | 置き場所 |
|---|---|
| Python | `domain/repositories.py`（ドメイン層） |
| Go | `internal/usecase/ports.go`（ユースケース層） |

なお Python でも、リポジトリ**以外**のポート（`Clock`、`PaymentGateway`、`IdGenerator`、
`UnitOfWork`）は `application/ports.py` に置いています。
「集約の永続化」はドメインの語彙ですが、「時計」や「決済代行」はそうではないからです。

## 理由

依存の矢印はどちらでも内向きで、クリーンアーキテクチャとしては等価です。
そうであれば、**その言語を書く人にとって自然なほうを選ぶ**ほうが、
コードを読む人の負担が小さくなります。

Go で `domain` パッケージにリポジトリのインターフェースを置くと、
Go を書く人には「なぜここに？」と映ります。
逆に Python で `application` にリポジトリを置くと、
DDD の文脈を持つ人には違和感があります。

**アーキテクチャの原則は、言語のイディオムを上書きする理由にはなりません。**

## 結果

**良かったこと**: どちらのコードも、その言語の読者にとって素直に読めます。

**引き受けたこと**: 2 言語を並べて読むと、対応が 1 対 1 ではありません。
この ADR がその説明になっています。

**見つかったこと**: Go では `UnitOfWork` だけが例外になりました。

```go
type UnitOfWork interface {
    Subscriptions() SubscriptionRepository   // インターフェースを返す
}
```

Go の戻り値の型は共変ではないため、実装側も `usecase.SubscriptionRepository` を
返すと書かねばならず、`infra` パッケージが `usecase` を import します。

Python では `Protocol` を読み取り専用プロパティにすることで共変になり、
実装側は何も import せずに済みました。

```python
class UnitOfWork(Protocol):
    @property
    def subscriptions(self) -> SubscriptionRepository: ...
```

依存の向きとしてはどちらも内向きなので問題ありませんが、
「実装は抽象を知らなくてよい」の徹底度には差が出ます。

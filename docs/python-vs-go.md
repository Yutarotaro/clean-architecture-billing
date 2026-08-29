# Python と Go の比較

同じドメイン、同じユースケース、同じ API を 2 つの言語で実装しました。
構造は同じですが、**言語の慣習が設計判断を変える**箇所がいくつかあります。

## 対応表

| | Python | Go |
|---|---|---|
| ドメイン層 | `src/billing/domain/` | `internal/domain/` |
| ユースケース層 | `src/billing/application/` | `internal/usecase/` |
| インフラ層 | `src/billing/infrastructure/` | `internal/infra/` |
| HTTP | `src/billing/presentation/` (FastAPI) | `internal/adapter/http/` (net/http) |
| 合成ルート | `presentation/container.py` | `internal/app/container.go` |
| 永続化 | memory / SQLite / **DynamoDB** | memory / SQLite |
| 値オブジェクト | `@dataclass(frozen=True, slots=True)` | 値型の `struct` |
| 厳密な比率 | `fractions.Fraction` | `math/big.Rat` |
| 抽象 | `typing.Protocol` | `interface` |
| エラー | 例外の階層 | 番兵エラー + `errors.Is` |
| テスト | pytest のパラメータ化 | table-driven + サブテスト |

## 違い 1: インターフェースをどこに置くか

これが最も大きな違いです。

=== "Python"

    リポジトリのインターフェースを**ドメイン層**に置いています（`domain/repositories.py`）。
    集約単位のリポジトリはドメインの語彙の一部だ、という DDD の考え方です。

    ```python
    class SubscriptionRepository(Protocol):
        def get(self, subscription_id: SubscriptionId) -> Subscription | None: ...
        def add(self, subscription: Subscription) -> None: ...
    ```

    `Protocol` は構造的部分型なので、**実装側はこのモジュールを import しません**。
    `SqlSubscriptionRepository` は `billing.domain.repositories` を知らないまま、
    たまたま同じシグネチャのメソッドを持つことで要求を満たします。

=== "Go"

    リポジトリのインターフェースを**ユースケース層**に置いています（`usecase/ports.go`）。
    「インターフェースは実装側ではなく利用側で定義する」という Go の慣習です。

    ```go
    type SubscriptionRepository interface {
        Get(ctx context.Context, id domain.SubscriptionID) (*domain.Subscription, error)
        Add(ctx context.Context, s *domain.Subscription) error
    }
    ```

    Go のインターフェースも構造的部分型なので、実装側は原則 import 不要です。

どちらも依存の矢印は内向きで、クリーンアーキテクチャとしては等価です。
詳細な理由は [ADR-0002](adr/0002-where-interfaces-live.md)。

### ただし Go では 1 箇所だけ import が必要になる

`UnitOfWork` は**インターフェースを返すメソッド**を持ちます。

```go
type UnitOfWork interface {
    Subscriptions() SubscriptionRepository
    Invoices() InvoiceRepository
    Plans() PlanRepository
}
```

Go の戻り値の型は共変ではないので、実装側も `usecase.SubscriptionRepository` を
返すと書かねばならず、**`infra` パッケージが `usecase` を import します**。

```go
func (u *UnitOfWork) Subscriptions() usecase.SubscriptionRepository { return u.subs }
```

Python では `UnitOfWork` を読み取り専用プロパティの `Protocol` にすることで、
実装側は何も import せずに済みました。
依存の向きとしてはどちらも内向きなので問題ありませんが、
**「実装は抽象を知らなくてよい」の徹底度には差が出ます**。

## 違い 2: トランザクション境界の書き方

=== "Python"

    コンテキストマネージャがそのまま使えます。

    ```python
    with self._uow_factory() as uow:
        subscription = uow.subscriptions.get(subscription_id)
        ...
        uow.commit()
    ```

    `__exit__` で「commit されていなければ rollback」を保証します。
    自動 commit にしていないのは、途中で `return` したときに意図せず
    commit される事故を防ぐためです。

=== "Go"

    `defer` で後始末を予約します。

    ```go
    func inTransaction(ctx context.Context, factory UnitOfWorkFactory, fn func(UnitOfWork) error) error {
        uow, err := factory(ctx)
        if err != nil {
            return err
        }
        defer func() { _ = uow.Rollback() }()
        return fn(uow)
    }
    ```

    `fn` がどこで `return` しても、パニックしても、開きっぱなしにはなりません。
    Commit 済みの `Rollback` は何もしない約束です。

## 違い 3: エラーの表現

=== "Python"

    例外の階層で表現します。より具体的なハンドラが先に照合されるので、
    `IllegalTransition` と `DomainError` を別々の HTTP ステータスに割り当てられます。

    ```python
    @app.exception_handler(IllegalTransition)
    async def _illegal_transition(_, exc): return _error(409, ...)

    @app.exception_handler(DomainError)
    async def _domain_error(_, exc): return _error(422, ...)
    ```

=== "Go"

    番兵エラーを `%w` で包み、`errors.Is` で判別します。

    ```go
    func Invalid(format string, args ...any) error {
        return fmt.Errorf("%w: %s", ErrInvariantViolation, fmt.Sprintf(format, args...))
    }
    ```

    ```go
    switch {
    case errors.Is(err, usecase.ErrNotFound):
        return http.StatusNotFound, "not_found"
    case errors.Is(err, domain.ErrIllegalTransition):
        return http.StatusConflict, "illegal_state"
    ...
    }
    ```

## 違い 4: 金額の演算

Python は演算子オーバーロードで自然に書けます。

```python
total = total + line.amount        # 通貨違いは CurrencyMismatch
```

Go には演算子オーバーロードがないので、エラーを返すメソッドになります。

```go
total, err = total.Add(line.Amount)
if err != nil {
    return Money{}, err
}
```

冗長ですが、**通貨違いを黙って許すと「1000 円 + 10 ドル = 1010」がそのまま
請求書に載る**ので、ここは静かに間違ってはいけない場所です。

## 違い 5: DI

Python 側は `Container` データクラスに `cached_property` でユースケースを並べ、
Go 側はコンストラクタ関数の引数で受け取ります。

どちらも **DI コンテナライブラリは使っていません**。
FastAPI の `Depends` にユースケースの組み立てを書かなかったのは、
配線が HTTP 層に固定されてバッチや CLI から再利用できなくなるからです。

## どちらが「クリーンアーキテクチャ向き」か

**Go のほうが、依存性逆転を自分の手でやっている実感が強く出ます。**
FastAPI は `Depends` と Pydantic が強力なぶん、フレームワークがレイヤー境界を
勝手に引いてくれるので、境界を意識しないままでもそれなりに動いてしまいます。

一方 Python は、`Protocol` の構造的部分型と読み取り専用プロパティによって、
**「実装は抽象を import しない」を Go より徹底できます**。

教材として並べる価値があるのはこの非対称性です。
どちらか一方だけを見ていると、それが言語の都合なのか設計の要請なのかが分かりません。

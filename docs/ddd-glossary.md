# DDD の用語

このサンプルは、レイヤー分割（クリーンアーキテクチャ）と **DDD（ドメイン駆動設計）の
戦術的パターン**を組み合わせて書かれています。ここでは実際に使っている用語を、
コードのどこにあるかと対応させて説明します。

!!! note "2 つは別物です"
    クリーンアーキテクチャは「依存の向き」の話、DDD は「ドメインをどう表現するか」の話で、
    どちらか一方だけでも成立します。ただ相性がよく、
    **クリーンアーキテクチャの一番内側（ドメイン層）に何を書くか**を決めるのに
    DDD の語彙が便利なので、併用されることが多い、という関係です。

## 一覧

| 用語 | 一言でいうと | このプロジェクトでの例 |
|---|---|---|
| [ユビキタス言語](#ユビキタス言語) | 業務の言葉でコードを書く | `expire_if_grace_over` |
| [不変条件](#不変条件) | いつでも成り立っていなければならない条件 | 「解約済みからは戻れない」 |
| [値オブジェクト](#値オブジェクト) | 値が同じなら同じもの。不変 | `Money`、`BillingPeriod` |
| [エンティティ](#エンティティ) | ID で同一性が決まる。状態が変わる | `Subscription`、`Invoice` |
| [集約](#集約) | 一貫性を保つ単位 | `Subscription` / `Invoice` / `Plan` の 3 つ |
| [集約ルート](#集約ルート) | 集約に触れる唯一の入口 | `Subscription` そのもの |
| [ファクトリ](#ファクトリ) | 正しい状態の集約を作る | `Subscription.subscribe` |
| [リポジトリ](#リポジトリ) | 集約を出し入れする | `SubscriptionRepository` |
| [ドメインサービス](#ドメインサービス) | どのエンティティにも属さない計算 | `prorate`（日割り） |
| [ドメインイベント](#ドメインイベント) | 起きたことの記録 | `SubscriptionEvent` |

---

## ユビキタス言語

**業務の担当者とプログラマが、同じ言葉を使う。** その言葉をそのままコードの名前にします。

課金の担当者は「猶予期間を過ぎたら解約になる」と言います。だからメソッド名はこうなります。

```python
def expire_if_grace_over(self, *, at: datetime, grace: timedelta = GRACE_PERIOD) -> bool:
```

これが `update_status(2)` や `process(flag)` だったら、担当者はコードを読んで確認できません。
仕様の食い違いは、たいてい**言葉が翻訳される場所**で起きます。翻訳をなくすのが狙いです。

このプロジェクトのメソッド名はすべてこの方針で付けています。
`change_plan`、`mark_payment_failed`、`settle_without_payment`、`renew`。
どれも課金業務の言葉であって、プログラマの言葉ではありません。

## 不変条件

**いつ見ても成り立っていなければならない条件。** 英語では invariant。

たとえば「解約済み（`canceled`）の契約は、二度とプラン変更できない」。
これを守る場所を 1 箇所に決めるのが、後述する集約の役割です。

```python
if self.is_terminated:
    raise IllegalTransition("subscription", self.status, "change plan of")
```

破られたときに投げる例外も、ドメイン層に用意してあります。

| 例外 | 意味 |
|---|---|
| `InvariantViolation` | 生成時点で成り立つべき条件を満たしていない（金額が float、期間が空） |
| `IllegalTransition` | その状態からは許されない遷移（解約済みを解約する） |
| `CurrencyMismatch` | 異なる通貨どうしの計算 |

## 値オブジェクト

**値が同じなら、同じものとして扱ってよいもの。** そして**不変**（作ったら変わらない）。

`Money(1000, "JPY")` が 2 つあったとき、それらを区別する意味はありません。
1,000 円は 1,000 円です。だから ID を持ちません。

```python
@dataclass(frozen=True, slots=True)   # frozen = 作った後は変更できない
class Money:
    amount: int
    currency: str = "JPY"
```

`frozen=True` にすると `money.amount = 500` が実行時エラーになります。
値を変えたいときは**新しいインスタンスを作る**。

```python
total = total + line.amount    # total 自身は変わらない。新しい Money が返る
```

不変にする利点は、**うっかり共有されて壊れることがない**ことです。
ある請求書の金額を書き換えたら別の請求書の金額も変わった、という事故が起こりえません。

このプロジェクトの値オブジェクト:

| 値オブジェクト | 表しているもの |
|---|---|
| `Money` | 金額（最小単位の整数 + 通貨） |
| `BillingPeriod` | 請求期間 `[start, end)` |
| `Proration` | 日割りの内訳（credit と charge） |
| `InvoiceLine` | 請求書の明細 1 行 |

**値オブジェクトは、ただのデータ入れ物ではありません。** ルールを持ちます。
`Money.scale()` に丸めの方針が閉じ込められているのがその例です。

```python
def scale(self, ratio: Fraction) -> Money:
    """丸めは floor で統一する。日割りでは事業者側ではなく利用者側に
    有利な方向に倒す、という方針をここで一箇所に閉じ込めている。"""
```

「日割りの端数をどう丸めるか」は業務上の判断です。それがコードの 1 箇所にあるので、
方針を変えたくなったら変えるのはこのメソッドだけで済みます。

## エンティティ

**ID で同一性が決まるもの。** 状態は時間とともに変わります。

契約は、プランが変わっても状態が `active` から `canceled` に変わっても、
**同じ契約**です。それを決めているのは中身ではなく `id` です。

```python
@dataclass(slots=True)          # frozen ではない = 状態が変わる
class Subscription:
    id: SubscriptionId          # これが同一性を決める
    customer_id: CustomerId
    plan_id: PlanId
    status: SubscriptionStatus
    current_period: BillingPeriod
    ...
```

### 値オブジェクトとの違い

| | 値オブジェクト | エンティティ |
|---|---|---|
| 同一性 | 値が同じなら同じ | ID が同じなら同じ |
| 変更 | 不変。変えるときは作り直す | 状態が変わる |
| 例 | 「1,000 円」 | 「契約 sub-1」 |

同じ「1,000 円」が 2 つあっても区別しませんが、
同じ内容の契約が 2 つあったら**それは別の契約**です。

!!! warning "このプロジェクトは、ここで原則から外れています"
    DDD の原則では、エンティティの等価性は **ID だけ**で判断すべきです。
    ところが `Subscription` は `@dataclass` のデフォルト（全フィールド比較）のままにしてあります。

    ```python
    # 本来はこう書くべき
    def __eq__(self, other): return self.id == other.id
    ```

    そうしていないのは、契約テストで「保存して読み直したものが元と同じか」を
    `assert loaded == original` の 1 行で書けるからです。ID だけの比較にすると、
    往復テストがフィールドを 1 つずつ突き合わせる形になり、
    **列を足したときに検証漏れが起きやすくなります**。

    トレードオフとして受け入れていますが、原則から外れている自覚のうえでの判断です。
    なお、集めたイベントだけは比較から外してあります（`compare=False`）。

## 集約

**一貫性を保つ単位。** 「ここに含まれるものは、いつ見ても必ず辻褄が合っている」と
保証する範囲を線で囲んだものです。

なぜ線を引くのか。オブジェクトが相互に参照し合うと、
**誰がルールを守る責任者なのか**が消えるからです。契約の状態を書き換えるコードが
あちこちに散ると、`canceled` なのに `current_period` が更新されている、
といった状態が生まれます。

このプロジェクトの集約は 3 つです。

| 集約 | 守っている不変条件 |
|---|---|
| `Subscription` | 状態遷移が仕様どおりであること |
| `Invoice` | 明細と合計・通貨が整合し、決着した請求書が再び変わらないこと |
| `Plan` | 不変。作ったら変わらない |

`InvoiceLine`（明細 1 行）は集約では**ありません**。`Invoice` の内部です。
明細だけを取り出して更新することはできず、リポジトリも存在しません。

### 境界をどう引いたか

判断基準は **ライフサイクルの違い**です。

**`Subscription` と `Invoice` を分けた理由**: 発行済みの請求書は、
契約の現在の状態とは独立した記録だからです。契約を解約したからといって、
過去の請求書が消えては困ります。会計上の記録と、いま生きている契約は、別の寿命を持ちます。

**`Subscription` が `Plan` を実体で持たない理由**: 別の集約だからです。
持たせると「契約を 1 件読むためにプランも必ず読む」という結合が生まれます。
だから ID だけ持ちます。

```python
class Subscription:
    plan_id: PlanId          # Plan そのものではなく ID
```

プランの中身が必要な操作では、**ユースケースが読んで引数で渡します**。

```python
current_plan = uow.plans.get(subscription.plan_id)
new_plan = uow.plans.get(PlanId(command.new_plan_id))
proration = subscription.change_plan(current_plan=current_plan, new_plan=new_plan, at=now)
```

`change_plan` の引数が 2 つの `Plan` になっているのは、この設計の帰結です。

### 集約が決めること

線を引くと、3 つのことが自動的に決まります。

1. **トランザクションの境界** — 原則「1 トランザクションにつき 1 集約」
2. **並行更新の単位** — 楽観ロックのバージョンは集約ごと（[ADR-0004](adr/0004-optimistic-locking.md)）
3. **リポジトリの粒度** — 集約 1 つにつきリポジトリ 1 つ

!!! info "このプロジェクトは原則を 1 つ破っています"
    `ChangePlan` は `Subscription` と `Invoice` を**同じトランザクションで**更新します。
    厳密な DDD なら「契約を更新して commit → ドメインイベント経由で請求書を作る」
    とすべきところです。

    そうしなかったのは、差額の請求書が作られないまま契約だけプランが変わる状態を
    許容したくなく、かつイベント駆動を入れるとサンプルの主題から遠ざかるためです。
    DynamoDB の `TransactWriteItems` に両方を載せられる範囲でもあります。

    原則は目的ではなく手段です。破るなら理由を言えるように、という整理です。

## 集約ルート

**集約に外から触れる、唯一の入口。** このプロジェクトでは `Subscription` と
`Invoice` と `Plan` そのものです。

大事なのは、**外から直接フィールドを書き換えるコードが存在しない**ことです。

```python
# こう書かれている場所はどこにもない
subscription.plan_id = "pro"
subscription.status = SubscriptionStatus.CANCELED
```

必ず集約ルートのメソッドを通ります。

```python
proration = subscription.change_plan(current_plan=basic, new_plan=pro, at=now)
```

だから「解約済みの契約はプラン変更できない」が**構造的に**守られます。
検査を書き忘れる場所が存在しないからです。

## ファクトリ

**正しい状態の集約を作る役目。** コンストラクタが複雑になるときに切り出します。

```python
@classmethod
def subscribe(cls, *, id, customer_id, plan, at, trial=None) -> Subscription:
    """新規契約を開始する。"""
```

`Subscription(...)` と直接書くと、`status` や `current_period` を呼び出し側が
組み立てることになり、**間違った組み合わせ**（`trialing` なのに `trial_end` が `None`）
を作れてしまいます。ファクトリを通せば、出来上がるのは常に正しい状態です。

同じ理由で `Invoice.issue`、`BillingPeriod.starting_at`、`Money.zero` があります。

## リポジトリ

**集約を出し入れする窓口。** 「保存する」「取り出す」を、
データベースの言葉ではなく**コレクションのように**扱えるようにします。

```python
class SubscriptionRepository(Protocol):
    def get(self, subscription_id: SubscriptionId) -> Subscription | None: ...
    def add(self, subscription: Subscription) -> None: ...
    def save(self, subscription: Subscription) -> None: ...
    def list_due(self, at: datetime, *, limit: int = 100) -> list[Subscription]: ...
```

SQL も DynamoDB も出てきません。**インターフェースは内側（ドメイン層）**にあり、
実装は外側（インフラ層）にあります。これが依存性逆転です
（[レイヤーと依存の向き](architecture.md#依存性逆転はどこで起きているか)）。

**リポジトリは集約ごとに 1 つ**です。このプロジェクトに 3 つしかないのは、集約が 3 つだから。
`InvoiceLine` のリポジトリが存在しないのは、それが集約ではないからです。

## ドメインサービス

**どのエンティティにも自然には属さない計算。** 日割りがそれです。

```python
def prorate(*, period: BillingPeriod, at: datetime,
            old_price: Money, new_price: Money) -> Proration:
```

「旧プランの未使用分を返し、新プランの残期間分を請求する」という計算は、
`Subscription` のものでも `Plan` のものでも `Money` のものでもありません。
だから純粋関数として切り出しています。

入力も出力も値オブジェクトだけで、DB もネットワークも時計も触りません。
**だからテストが表を書くだけで済みます。**

!!! danger "「サービス」を安易に作らない"
    どのエンティティにも属さないから、と何でもサービスにすると、
    エンティティがただのデータ入れ物になり、ロジックがサービスに集まります
    （**貧血ドメインモデル**と呼ばれる状態）。

    このプロジェクトでドメインサービスは `prorate` 1 つだけです。
    状態遷移も金額の丸めも、それぞれの集約と値オブジェクトが持っています。

## ドメインイベント

**起きたことの記録。** 集約は事実を残すだけで、それを何に使うかは外側が決めます。

```python
@dataclass(frozen=True, slots=True)
class SubscriptionEvent:
    name: str
    subscription_id: SubscriptionId
    occurred_at: datetime
```

```python
self._record("subscription.plan_changed", at)
```

なぜこうするのか。「解約されたらメールを送る」をドメインに書いてしまうと、
**ドメイン層がメール送信を知ることになる**からです。そうなるとビジネスルールの
テストがメールサーバーを必要とします。

集約は「解約された」という事実だけを残し、取り出すのは呼び出し側です。

```python
subscription.pull_events()
# [SubscriptionEvent(name="subscription.created", ...),
#  SubscriptionEvent(name="subscription.canceled", ...)]
```

!!! note "購読側は実装していません"
    このサンプルはイベントを記録するところまでです。実務ではここから
    メール送信や分析基盤への転送につながります
    （[あえて省いたもの](design-decisions.md#ドメインイベントの購読側)）。

## 関連する用語（DDD ではないもの）

### Unit of Work

**トランザクション境界を表す**パターン。DDD ではなく PoEAA（エンタープライズ
アプリケーションアーキテクチャパターン）由来ですが、リポジトリと組み合わせて使われます。

```python
with self._uow_factory() as uow:
    subscription = uow.subscriptions.get(subscription_id)
    ...
    uow.commit()
```

複数のリポジトリが**同じトランザクション**に参加することを保証します。
リポジトリごとに勝手に接続を張ると、「請求書は作られたが契約の状態は元のまま」
という半端な状態が生まれます。

### CQRS

**書き込みと読み取りで別の道を通す**という考え方。このプロジェクトでは、
書き込みが集約とリポジトリを通るのに対し、読み取りは `BillingQueries` が
集約を素通しで View に変換するだけ、という緩い形で入っています。

```python
class BillingQueries:
    def list_invoices(self, customer_id: str) -> list[InvoiceView]:
```

規模が大きくなれば、読み取り専用の非正規化ビューを別に持つ方向に伸ばせます。

---

## 入っていない DDD 要素

戦術的パターンは一通り使っていますが、**戦略的設計**のほうはほとんど入っていません。

| 用語 | 状況 |
|---|---|
| **境界づけられたコンテキスト** | 1 つしかありません。実務では「課金」「認証」「分析」で言葉の意味が変わるので、境界を切って別々のモデルを持ちます |
| **コンテキストマップ** | 上記が 1 つなので存在しません |
| **仕様パターン（Specification）** | 使っていません。絞り込み条件が `list_due` のような固定のメソッドで足りているためです |
| **腐敗防止層（ACL）** | 決済代行の adapter がそれに近い役割ですが、外部モデルの変換が単純なので明示的な層にはしていません |
| **イベントソーシング** | 状態を保存する方式です。イベントを記録はしますが、それを正とはしていません |

戦略的設計は「複数チーム・複数サービスでどう分けるか」の話なので、
**サンプル 1 つでは実演できません**。用語だけ知っておけば十分だと思います。

## もっと知りたい場合

- エリック・エヴァンス『ドメイン駆動設計』— 原典。戦略的設計の記述が中心
- ヴァーン・ヴァーノン『実践ドメイン駆動設計』— 戦術的パターンの実装寄り
- 『Cosmic Python』（*Architecture Patterns with Python*）— Python でのリポジトリ・UoW・イベントの実装。無料で読めます

このプロジェクトの構成は 3 番目に近いです。

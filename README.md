# クリーンアーキテクチャで作るサブスクリプション課金

[![CI](https://github.com/Yutarotaro/clean-architecture-billing/actions/workflows/ci.yml/badge.svg)](https://github.com/Yutarotaro/clean-architecture-billing/actions/workflows/ci.yml)
[![Docs](https://github.com/Yutarotaro/clean-architecture-billing/actions/workflows/pages.yml/badge.svg)](https://Yutarotaro.github.io/clean-architecture-billing/)

サブスクリプション課金を題材に、クリーンアーキテクチャを **Python/FastAPI** と **Go** の
2 言語で実装したサンプルです。Python 側は **インメモリ・SQLite・DynamoDB** の
3 つの永続化実装の上で、同じユースケースコードのまま動きます。

📖 **解説: <https://Yutarotaro.github.io/clean-architecture-billing/>**

## このサンプルの主張

「レイヤーを分けました」で終わらせず、**分けたことが機能しているかを機械的に検証**します。

| 問い | 検証方法 |
|---|---|
| ドメイン層は本当にフレームワークから独立しているか | AST / `go/parser` で import を解析し、依存の向きをテストで検査 |
| 永続化技術は本当に差し替えられるか | 同じ契約テストを SQL・key-value NoSQL・インメモリの 3 実装で実行 |
| 言語が変わっても同じ構造が成立するか | Python と Go で同じドメインを実装し、差分を ADR に記録 |

結論は [永続化は本当に閉じ込められるか](https://Yutarotaro.github.io/clean-architecture-billing/persistence-portability/) にまとめてあります。
要約すると **インターフェースは閉じ込められるが、暗黙の前提は漏れる**（トランザクションの
意味論、サイズ上限、悲観ロックの不在、衝突が判明するタイミング）。

## 動かす

### Python

```bash
cd python
uv sync
uv run uvicorn billing.presentation.main:app --reload
```

<http://127.0.0.1:8000/docs> で OpenAPI の画面が開きます。

```bash
uv run pytest          # 262 ケース（3 つの永続化実装ぶん）
uv run ruff check .
uv run mypy
```

### Go

```bash
cd go
go run ./cmd/api       # http://127.0.0.1:8080/health
go test ./...          # 224 ケース（2 つの永続化実装ぶん）
```

### 永続化を切り替える

アプリケーションのコードは 1 行も変わりません。

```bash
BILLING_PERSISTENCE=sqlite uv run uvicorn billing.presentation.main:app
BILLING_PERSISTENCE=sqlite go run ./cmd/api
```

DynamoDB を使う場合（Python のみ、DynamoDB Local などのエンドポイントが必要）:

```bash
BILLING_PERSISTENCE=dynamodb BILLING_DYNAMO_ENDPOINT=http://localhost:8000 \
  uv run uvicorn billing.presentation.main:app
```

## ドメインの題材

CRUD だけの題材ではレイヤーが全部素通しになるので、
**外側の都合では決められない不変条件**があるものを選びました。

- 期間の途中でプランを変えたら、旧プランの未使用分を返し、新プランの残期間分を請求する（日割り）
- 支払いに失敗したら即解約ではなく、14 日の猶予を置いてから解約する
- 同じ請求が二度実行されてはいけない（冪等性）
- 解約済みの契約は、二度と課金対象にならない

## 構成

```
python/src/billing/          go/internal/
├── domain/                  ├── domain/          標準ライブラリのみ
├── application/             ├── usecase/         ユースケースとポート定義
├── infrastructure/          ├── infra/           DB・決済代行・時計の実装
│   ├── persistence/memory/  │   ├── memory/
│   ├── persistence/sql/     │   ├── sqlite/
│   └── persistence/dynamo/  │   └── storage/
└── presentation/            ├── adapter/http/    net/http のハンドラ
                             └── app/             合成ルート
docs/                        MkDocs（GitHub Pages に公開）
```

## ドキュメント

- [レイヤーと依存の向き](https://Yutarotaro.github.io/clean-architecture-billing/architecture/)
- [ドメインモデル](https://Yutarotaro.github.io/clean-architecture-billing/domain-model/)
- [永続化は本当に閉じ込められるか](https://Yutarotaro.github.io/clean-architecture-billing/persistence-portability/)
- [Python と Go の比較](https://Yutarotaro.github.io/clean-architecture-billing/python-vs-go/)
- [テスト戦略](https://Yutarotaro.github.io/clean-architecture-billing/testing/)
- [あえて省いたもの](https://Yutarotaro.github.io/clean-architecture-billing/design-decisions/)
- [ADR 一覧](https://Yutarotaro.github.io/clean-architecture-billing/adr/)

## ライセンス

MIT

# ADR-0001: レイヤー境界を 4 つに切り、依存の向きをテストで守る

**状態**: 採用

## 文脈

クリーンアーキテクチャの説明は「同心円の図」で終わりがちです。
しかし図があっても、締切の前日にドメイン層から SQLAlchemy を import する人は必ず現れます。
そしてそれは 1 行の diff としてレビューを通過します。

## 決定

層を 4 つに切り、**各層が import してよい相手をテストで宣言**します。

```python
ALLOWED_EXTERNAL: dict[str, set[str]] = {
    "domain": set(),        # 標準ライブラリだけ
    "application": set(),
    "infrastructure": {"sqlalchemy", "boto3", "botocore"},
    "presentation": {"fastapi", "pydantic", "starlette"},
}
```

Python は AST を、Go は `go/parser` を使って import を集め、違反があれば失敗させます。

## 理由

**ルールは、破ったときに壊れる形で書かないと守られません。**

代替案として `import-linter` や `go-arch-lint` のような専用ツールもあります。
自前で書いたのは、依存を 1 つ増やす理由が薄いのと、
検査そのものが 100 行程度で読み切れる量だからです。
サンプルとしては「何をどう検査しているか」がその場で読めるほうが価値があります。

## 結果

**良かったこと**: 実際に違反を 1 件検出しました。
合成ルート（`presentation/container.py`）が DynamoDB クライアントを作るために
`import boto3` していたのを、インフラ層の `create_dynamo_client` に移しました。

**引き受けたこと**: 新しいライブラリを使うたびに、許可リストへの追加が必要になります。
これは摩擦ですが、意図的な摩擦です。
「なぜこの層でそのライブラリが要るのか」を一度考える機会になります。

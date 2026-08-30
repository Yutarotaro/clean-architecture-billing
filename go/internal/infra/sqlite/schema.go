// Package sqlite は database/sql を使った永続化実装を持つ。
//
// ORM は使わず、行とドメインオブジェクトの変換を自分で書いている。ドメインの構造体に
// ORM のタグや基底型を一切付けたくないためで、その代償として mappers.go の手書きコードを
// 引き受けている（docs/adr/0003-hand-written-mappers.md）。
package sqlite

import (
	"context"
	"database/sql"
	"fmt"

	_ "modernc.org/sqlite" // cgo なしで動く SQLite ドライバ
)

// ここが「ドメインモデルとテーブルは別物である」ことが最も見える場所である。
// Money は 1 つの値オブジェクトだが、テーブルでは amount と currency の 2 列になる。
// BillingPeriod も start と end の 2 列に分かれる。
const schemaDDL = `
CREATE TABLE IF NOT EXISTS plans (
    id             TEXT PRIMARY KEY,
    name           TEXT    NOT NULL,
    price_amount   INTEGER NOT NULL,
    price_currency TEXT    NOT NULL,
    interval       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id                   TEXT PRIMARY KEY,
    customer_id          TEXT    NOT NULL,
    plan_id              TEXT    NOT NULL REFERENCES plans(id),
    status               TEXT    NOT NULL,
    period_start         TEXT    NOT NULL,
    period_end           TEXT    NOT NULL,
    cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
    past_due_since       TEXT,
    canceled_at          TEXT,
    trial_end            TEXT,
    -- 楽観ロック用。ドメインの構造体には持たせず、リポジトリだけが管理する。
    version              INTEGER NOT NULL DEFAULT 1
);

-- 更新バッチが毎回フルスキャンしないための索引。
CREATE INDEX IF NOT EXISTS ix_subscriptions_due ON subscriptions(status, period_end);
CREATE INDEX IF NOT EXISTS ix_subscriptions_customer ON subscriptions(customer_id);

CREATE TABLE IF NOT EXISTS invoices (
    id              TEXT PRIMARY KEY,
    customer_id     TEXT    NOT NULL,
    subscription_id TEXT    NOT NULL REFERENCES subscriptions(id),
    status          TEXT    NOT NULL,
    currency        TEXT    NOT NULL,
    issued_at       TEXT,
    paid_at         TEXT,
    -- 冪等性はアプリケーションの if 文ではなく、まず DB の一意制約で守る。
    -- 二重発行を防ぐ最後の砦がここにあると、競合状態が起きても壊れ方が静かにならない。
    idempotency_key TEXT UNIQUE,
    version         INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS ix_invoices_customer ON invoices(customer_id, issued_at);

-- 決着していない請求書を拾い直すバッチのための索引。
CREATE INDEX IF NOT EXISTS ix_invoices_unsettled ON invoices(status, issued_at);

CREATE TABLE IF NOT EXISTS invoice_lines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id  TEXT    NOT NULL REFERENCES invoices(id),
    position    INTEGER NOT NULL,
    description TEXT    NOT NULL,
    amount      INTEGER NOT NULL,
    currency    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_invoice_lines_invoice ON invoice_lines(invoice_id, position);
`

// Open はデータベースを開き、必要な PRAGMA を設定する。
//
//   - foreign_keys: SQLite の外部キーは既定で無効。これを忘れると「参照先のない
//     データが平然と入る DB」で結合テストを書くことになる。
//   - journal_mode=WAL: 既定のロールバックジャーナルでは、読み取りトランザクションが
//     開いているあいだ書き込みが SQLITE_BUSY で弾かれる。楽観ロックの検証には
//     「読んだまま別の接続が書く」状況が必要なので、読み手と書き手が並行できる
//     WAL にしている。本番でも SQLite を使うならまず WAL にする。
//   - busy_timeout: それでも競合したときに即座に諦めず、少し待つ。
func Open(dsn string) (*sql.DB, error) {
	db, err := sql.Open("sqlite",
		dsn+"?_pragma=foreign_keys(1)&_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)")
	if err != nil {
		return nil, fmt.Errorf("sqlite: open %q: %w", dsn, err)
	}
	if err := db.Ping(); err != nil {
		return nil, fmt.Errorf("sqlite: ping %q: %w", dsn, err)
	}
	return db, nil
}

// CreateSchema はテーブルを作る。
//
// 本番ではマイグレーションツールに置き換えることを想定している。サンプルの実行と
// 結合テストを 1 コマンドで始められるように用意してある。
func CreateSchema(ctx context.Context, db *sql.DB) error {
	if _, err := db.ExecContext(ctx, schemaDDL); err != nil {
		return fmt.Errorf("sqlite: create schema: %w", err)
	}
	return nil
}

package sqlite

import (
	"context"
	"database/sql"
	"fmt"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/usecase"
)

// UnitOfWork は 1 つのトランザクションを 3 つのリポジトリで共有する。
//
// リポジトリごとに勝手に接続を張ると、同じユースケースの中の書き込みが別々の
// トランザクションに散らばる。「請求書は作られたが契約の状態は元のまま」という
// 半端な状態が本番で生まれるのは、たいていこれが原因である。
type UnitOfWork struct {
	db  *sql.DB
	ctx context.Context
	tx  *sql.Tx

	plans    *planRepo
	subs     *subscriptionRepo
	invoices *invoiceRepo
}

// NewUnitOfWorkFactory は *sql.DB に対する UnitOfWork の生成関数を返す。
func NewUnitOfWorkFactory(db *sql.DB) usecase.UnitOfWorkFactory {
	return func(ctx context.Context) (usecase.UnitOfWork, error) {
		tx, err := db.BeginTx(ctx, nil)
		if err != nil {
			return nil, fmt.Errorf("sqlite: begin: %w", err)
		}
		u := &UnitOfWork{db: db, ctx: ctx, tx: tx}
		u.bind()
		return u, nil
	}
}

func (u *UnitOfWork) bind() {
	u.plans = &planRepo{tx: u.tx}
	u.subs = &subscriptionRepo{tx: u.tx, versions: newVersionTracker("subscription")}
	u.invoices = &invoiceRepo{tx: u.tx, versions: newVersionTracker("invoice")}
}

// Plans はプランのリポジトリを返す。
func (u *UnitOfWork) Plans() usecase.PlanRepository { return u.plans }

// Subscriptions は契約のリポジトリを返す。
func (u *UnitOfWork) Subscriptions() usecase.SubscriptionRepository { return u.subs }

// Invoices は請求書のリポジトリを返す。
func (u *UnitOfWork) Invoices() usecase.InvoiceRepository { return u.invoices }

// Commit はトランザクションを確定し、続けて使えるよう新しいトランザクションを開く。
func (u *UnitOfWork) Commit() error {
	if u.tx == nil {
		return fmt.Errorf("sqlite: commit outside of a transaction")
	}
	if err := u.tx.Commit(); err != nil {
		return fmt.Errorf("sqlite: commit: %w", err)
	}
	tx, err := u.db.BeginTx(u.ctx, nil)
	if err != nil {
		return fmt.Errorf("sqlite: begin after commit: %w", err)
	}
	u.tx = tx
	// commit でバージョン追跡の前提が変わるため、リポジトリを作り直す。
	u.bind()
	return nil
}

// Rollback はトランザクションを巻き戻す。Commit 済みの場合は開き直したものを閉じる。
func (u *UnitOfWork) Rollback() error {
	if u.tx == nil {
		return nil
	}
	err := u.tx.Rollback()
	u.tx = nil
	if err != nil && err != sql.ErrTxDone {
		return fmt.Errorf("sqlite: rollback: %w", err)
	}
	return nil
}

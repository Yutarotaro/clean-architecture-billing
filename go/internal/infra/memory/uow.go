package memory

import (
	"context"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/usecase"
)

// UnitOfWork は溜めた変更を Commit のときだけ Database に反映する。
//
// 「テスト用だからトランザクションはなくていい」とはしない。commit を書き忘れた
// ユースケースがテストでは通って本番で壊れる、という事故がまさにここで防がれる。
type UnitOfWork struct {
	db       *Database
	staging  *staging
	plans    *planRepo
	subs     *subscriptionRepo
	invoices *invoiceRepo
}

// NewUnitOfWorkFactory は Database に対する UnitOfWork の生成関数を返す。
//
// 戻り値が usecase.UnitOfWorkFactory であることで、合成ルートはこの関数を
// ユースケースにそのまま渡せる。ユースケースは memory パッケージを知らない。
func NewUnitOfWorkFactory(db *Database) usecase.UnitOfWorkFactory {
	return func(context.Context) (usecase.UnitOfWork, error) {
		return newUnitOfWork(db), nil
	}
}

func newUnitOfWork(db *Database) *UnitOfWork {
	u := &UnitOfWork{db: db}
	u.begin()
	return u
}

func (u *UnitOfWork) begin() {
	u.staging = newStaging()
	u.plans = &planRepo{db: u.db, staging: u.staging}
	u.subs = &subscriptionRepo{
		db: u.db, staging: u.staging, versions: newVersionTracker("subscription"),
	}
	u.invoices = &invoiceRepo{
		db: u.db, staging: u.staging, versions: newVersionTracker("invoice"),
	}
}

// Plans はプランのリポジトリを返す。
func (u *UnitOfWork) Plans() usecase.PlanRepository { return u.plans }

// Subscriptions は契約のリポジトリを返す。
func (u *UnitOfWork) Subscriptions() usecase.SubscriptionRepository { return u.subs }

// Invoices は請求書のリポジトリを返す。
func (u *UnitOfWork) Invoices() usecase.InvoiceRepository { return u.invoices }

// Commit は溜めた変更を反映する。
func (u *UnitOfWork) Commit() error {
	u.db.mu.Lock()
	for id, plan := range u.staging.plans {
		u.db.plans[id] = plan
	}
	for id, s := range u.staging.subscriptions {
		u.db.subscriptions[id] = s
	}
	for id, invoice := range u.staging.invoices {
		u.db.invoices[id] = invoice
	}
	for id, version := range u.staging.versions {
		u.db.versions[id] = version
	}
	u.db.mu.Unlock()

	// commit 後も同じ UnitOfWork を使い続けられるように作り直す。バージョン追跡の
	// 前提が変わるため、リポジトリごと入れ替える。
	u.begin()
	return nil
}

// Rollback は溜めた変更を捨てる。Commit 済みの場合は何も起きない。
func (u *UnitOfWork) Rollback() error {
	u.begin()
	return nil
}

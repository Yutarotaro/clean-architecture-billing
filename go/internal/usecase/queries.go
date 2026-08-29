package usecase

import (
	"context"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
)

// Queries は読み取り専用の問い合わせ。
//
// 「一覧を返す」だけの操作にユースケース 1 つぶんの器を作っても重いだけなので、
// 読み取りはここにまとめている。書き込み側が集約を通して不変条件を守るのに対し、
// こちらは集約を素通しで View に変換するだけで、状態を変えない。
type Queries struct {
	factory UnitOfWorkFactory
}

// NewQueries は問い合わせを組み立てる。
func NewQueries(factory UnitOfWorkFactory) *Queries {
	return &Queries{factory: factory}
}

// ListPlans はプラン一覧を返す。
func (q *Queries) ListPlans(ctx context.Context) ([]PlanView, error) {
	var views []PlanView
	err := inTransaction(ctx, q.factory, func(uow UnitOfWork) error {
		plans, err := uow.Plans().ListAll(ctx)
		if err != nil {
			return err
		}
		views = make([]PlanView, 0, len(plans))
		for _, plan := range plans {
			views = append(views, planView(plan))
		}
		return nil
	})
	return views, err
}

// GetSubscription は契約を 1 件返す。
func (q *Queries) GetSubscription(ctx context.Context, id string) (SubscriptionView, error) {
	var view SubscriptionView
	err := inTransaction(ctx, q.factory, func(uow UnitOfWork) error {
		subscription, err := uow.Subscriptions().Get(ctx, domain.SubscriptionID(id))
		if err != nil {
			return err
		}
		if subscription == nil {
			return NotFound("subscription", id)
		}
		view = subscriptionView(subscription)
		return nil
	})
	return view, err
}

// ListInvoices は顧客の請求書一覧を返す。
func (q *Queries) ListInvoices(ctx context.Context, customerID string) ([]InvoiceView, error) {
	var views []InvoiceView
	err := inTransaction(ctx, q.factory, func(uow UnitOfWork) error {
		invoices, err := uow.Invoices().ListForCustomer(ctx, domain.CustomerID(customerID))
		if err != nil {
			return err
		}
		views = make([]InvoiceView, 0, len(invoices))
		for _, invoice := range invoices {
			view, err := invoiceView(invoice)
			if err != nil {
				return err
			}
			views = append(views, view)
		}
		return nil
	})
	return views, err
}

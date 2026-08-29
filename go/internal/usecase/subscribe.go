package usecase

import (
	"context"
	"fmt"
	"time"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
)

// SubscribeCommand は新規契約の要求。
type SubscribeCommand struct {
	CustomerID string
	PlanID     string
	TrialDays  int
}

// SubscribeResult は新規契約の結果。
type SubscribeResult struct {
	Subscription  SubscriptionView
	Invoice       *InvoiceView
	PaymentFailed bool
}

// SubscribeToPlan は顧客をプランに契約させる。試用期間なしなら初回請求まで行う。
type SubscribeToPlan struct {
	factory UnitOfWorkFactory
	clock   Clock
	ids     IDGenerator
	gateway PaymentGateway
}

// NewSubscribeToPlan は依存をすべて明示的に受け取る。
//
// DI コンテナは使わない。何がどこに注入されているかが、この関数の引数を読むだけで
// 分かる状態を保つ。
func NewSubscribeToPlan(
	factory UnitOfWorkFactory, clock Clock, ids IDGenerator, gateway PaymentGateway,
) *SubscribeToPlan {
	return &SubscribeToPlan{factory: factory, clock: clock, ids: ids, gateway: gateway}
}

// Execute は契約を作り、必要なら初回請求を行う。
func (u *SubscribeToPlan) Execute(ctx context.Context, cmd SubscribeCommand) (SubscribeResult, error) {
	now := u.clock.Now()
	trial := time.Duration(cmd.TrialDays) * 24 * time.Hour

	var (
		subscription *domain.Subscription
		invoice      *domain.Invoice
	)

	if err := inTransaction(ctx, u.factory, func(uow UnitOfWork) error {
		plan, err := uow.Plans().Get(ctx, domain.PlanID(cmd.PlanID))
		if err != nil {
			return err
		}
		if plan == nil {
			return NotFound("plan", cmd.PlanID)
		}

		subscription, err = domain.Subscribe(
			domain.SubscriptionID(u.ids.NewID()),
			domain.CustomerID(cmd.CustomerID),
			*plan,
			now,
			trial,
		)
		if err != nil {
			return err
		}
		if err := uow.Subscriptions().Add(ctx, subscription); err != nil {
			return err
		}

		if subscription.Status == domain.StatusActive {
			period := subscription.CurrentPeriod
			invoice, err = domain.IssueInvoice(
				domain.InvoiceID(u.ids.NewID()),
				subscription.CustomerID,
				subscription.ID,
				[]domain.InvoiceLine{{
					Description: fmt.Sprintf("%s (%s 〜 %s)", plan.Name,
						period.Start.Format(time.DateOnly), period.End.Format(time.DateOnly)),
					Amount: plan.Price,
				}},
				plan.Price.Currency,
				now,
				// 契約 1 件につき初回請求は 1 通しかない。この形の鍵にしておけば、
				// 同じ要求が再送されても 2 通目は作られない。
				fmt.Sprintf("initial:%s", subscription.ID),
			)
			if err != nil {
				return err
			}
			if err := uow.Invoices().Add(ctx, invoice); err != nil {
				return err
			}
		}
		return uow.Commit()
	}); err != nil {
		return SubscribeResult{}, err
	}

	if invoice == nil {
		return SubscribeResult{Subscription: subscriptionView(subscription)}, nil
	}

	outcome, err := chargeInvoice(ctx, u.factory, u.gateway, u.clock, invoice.ID)
	if err != nil {
		return SubscribeResult{}, err
	}
	view, err := invoiceView(outcome.Invoice)
	if err != nil {
		return SubscribeResult{}, err
	}
	return SubscribeResult{
		Subscription:  subscriptionView(outcome.Subscription),
		Invoice:       &view,
		PaymentFailed: !outcome.Result.Succeeded,
	}, nil
}

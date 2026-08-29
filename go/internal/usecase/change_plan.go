package usecase

import (
	"context"
	"fmt"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
)

// ChangePlanCommand はプラン変更の要求。
type ChangePlanCommand struct {
	SubscriptionID string
	NewPlanID      string
	IdempotencyKey string
}

// ChangePlanResult はプラン変更の結果。
type ChangePlanResult struct {
	Subscription SubscriptionView
	Proration    ProrationView
	Invoice      *InvoiceView
}

// ChangePlan は契約中のプランを別のプランに変更し、差額を即時請求する。
//
// 日割りの計算そのものはドメインが行う。このクラスがやっているのは、必要な集約を
// 集めてきて、計算結果を請求書という別の集約に変換し、決済につなぐという「段取り」だけ。
// ユースケース層に if が並んで金額を計算し始めたら、ドメインに書くべきものが漏れている。
type ChangePlan struct {
	factory UnitOfWorkFactory
	clock   Clock
	ids     IDGenerator
	gateway PaymentGateway
}

// NewChangePlan はプラン変更ユースケースを組み立てる。
func NewChangePlan(
	factory UnitOfWorkFactory, clock Clock, ids IDGenerator, gateway PaymentGateway,
) *ChangePlan {
	return &ChangePlan{factory: factory, clock: clock, ids: ids, gateway: gateway}
}

// Execute はプランを変更する。同じ冪等キーでの再送は二重に課金しない。
func (u *ChangePlan) Execute(ctx context.Context, cmd ChangePlanCommand) (ChangePlanResult, error) {
	now := u.clock.Now()

	var (
		result   ChangePlanResult
		invoice  *domain.Invoice
		replayed bool
	)

	if err := inTransaction(ctx, u.factory, func(uow UnitOfWork) error {
		existing, err := uow.Invoices().FindByIdempotencyKey(ctx, cmd.IdempotencyKey)
		if err != nil {
			return err
		}
		if existing != nil {
			if string(existing.SubscriptionID) != cmd.SubscriptionID {
				return fmt.Errorf("%w: idempotency key %q was used for another subscription",
					ErrConflictingRequest, cmd.IdempotencyKey)
			}
			// 同じ要求が再送された。もう一度課金してはいけない。
			replayed = true
			result, err = replayResult(ctx, uow, cmd.SubscriptionID, existing)
			return err
		}

		subscription, err := uow.Subscriptions().Get(ctx, domain.SubscriptionID(cmd.SubscriptionID))
		if err != nil {
			return err
		}
		if subscription == nil {
			return NotFound("subscription", cmd.SubscriptionID)
		}

		currentPlan, err := requirePlan(ctx, uow, subscription.PlanID)
		if err != nil {
			return err
		}
		newPlan, err := requirePlan(ctx, uow, domain.PlanID(cmd.NewPlanID))
		if err != nil {
			return err
		}

		proration, err := subscription.ChangePlan(*currentPlan, *newPlan, now)
		if err != nil {
			return err
		}
		net, err := proration.Net()
		if err != nil {
			return err
		}

		if net.IsPositive() {
			invoice, err = domain.IssueInvoice(
				domain.InvoiceID(u.ids.NewID()),
				subscription.CustomerID,
				subscription.ID,
				[]domain.InvoiceLine{
					{Description: currentPlan.Name + " 未使用分", Amount: proration.Credit.Neg()},
					{Description: newPlan.Name + " 残期間分", Amount: proration.Charge},
				},
				net.Currency,
				now,
				cmd.IdempotencyKey,
			)
			if err != nil {
				return err
			}
			if err := uow.Invoices().Add(ctx, invoice); err != nil {
				return err
			}
		}

		if err := uow.Subscriptions().Save(ctx, subscription); err != nil {
			return err
		}
		view, err := prorationView(proration)
		if err != nil {
			return err
		}
		result = ChangePlanResult{Subscription: subscriptionView(subscription), Proration: view}
		return uow.Commit()
	}); err != nil {
		return ChangePlanResult{}, err
	}

	if replayed || invoice == nil {
		// 差額が 0 以下、つまり減額。ここでは返金も繰り越しも行わず、次の請求期間から
		// 新しい料金が適用される（docs/design-decisions.md を参照）。
		return result, nil
	}

	outcome, err := chargeInvoice(ctx, u.factory, u.gateway, u.clock, invoice.ID)
	if err != nil {
		return ChangePlanResult{}, err
	}
	view, err := invoiceView(outcome.Invoice)
	if err != nil {
		return ChangePlanResult{}, err
	}
	result.Subscription = subscriptionView(outcome.Subscription)
	result.Invoice = &view
	return result, nil
}

func requirePlan(ctx context.Context, uow UnitOfWork, id domain.PlanID) (*domain.Plan, error) {
	plan, err := uow.Plans().Get(ctx, id)
	if err != nil {
		return nil, err
	}
	if plan == nil {
		return nil, NotFound("plan", string(id))
	}
	return plan, nil
}

// replayResult は再送時に、保存済みの請求書から結果を復元する。
func replayResult(
	ctx context.Context, uow UnitOfWork, subscriptionID string, invoice *domain.Invoice,
) (ChangePlanResult, error) {
	subscription, err := uow.Subscriptions().Get(ctx, domain.SubscriptionID(subscriptionID))
	if err != nil {
		return ChangePlanResult{}, err
	}
	if subscription == nil {
		return ChangePlanResult{}, NotFound("subscription", subscriptionID)
	}
	view, err := invoiceView(invoice)
	if err != nil {
		return ChangePlanResult{}, err
	}
	return ChangePlanResult{
		Subscription: subscriptionView(subscription),
		Proration: ProrationView{
			Credit: MoneyView{Amount: -invoice.Lines[0].Amount.Amount, Currency: invoice.Currency},
			Charge: moneyView(invoice.Lines[1].Amount),
			Net:    view.Total,
		},
		Invoice: &view,
	}, nil
}

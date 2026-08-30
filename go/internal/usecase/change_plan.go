package usecase

import (
	"context"
	"fmt"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
)

// keyPrefix はクライアントが指定した冪等キーに付ける接頭辞。
//
// 内部で組み立てる鍵（"initial:<id>"、"renew:<id>:<iso>"）と名前空間を分ける。
// 分けないと Idempotency-Key: initial:sub-1 のような値で初回請求の請求書が
// 引き当てられ、明細の構造が違うまま復元されて壊れる。
const keyPrefix = "change:"

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
	// Invoice は差額が 0 以下でも必ず残るので、常に埋まる。
	Invoice InvoiceView
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
	key := keyPrefix + cmd.IdempotencyKey

	var (
		result      ChangePlanResult
		invoice     *domain.Invoice
		replayed    bool
		needsCharge bool
	)

	if err := inTransaction(ctx, u.factory, func(uow UnitOfWork) error {
		existing, err := uow.Invoices().FindByIdempotencyKey(ctx, key)
		if err != nil {
			return err
		}
		if existing != nil {
			// 同じ要求が再送された。もう一度課金してはいけない。
			replayed = true
			result, err = replayResult(ctx, uow, cmd, existing)
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

		// 差額が 0 以下でも請求書は必ず発行する。作らないと冪等キーを記録する
		// 場所がなくなり、再送が「すでにそのプランです」というエラーになる。
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
			key,
		)
		if err != nil {
			return err
		}
		needsCharge = net.IsPositive()
		if !needsCharge {
			// 減額、または試用中の変更。請求するものがないので、発行と同時に決着させる。
			// 返金や次回への繰り越しは行わない（docs/design-decisions.md を参照）。
			if err := invoice.SettleWithoutPayment(now); err != nil {
				return err
			}
		}
		if err := uow.Invoices().Add(ctx, invoice); err != nil {
			return err
		}

		if err := uow.Subscriptions().Save(ctx, subscription); err != nil {
			return err
		}

		prorView, err := prorationView(proration)
		if err != nil {
			return err
		}
		invView, err := invoiceView(invoice)
		if err != nil {
			return err
		}
		result = ChangePlanResult{
			Subscription: subscriptionView(subscription),
			Proration:    prorView,
			Invoice:      invView,
		}
		return uow.Commit()
	}); err != nil {
		return ChangePlanResult{}, err
	}

	if replayed || !needsCharge {
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
	result.Invoice = view
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

// replayResult は再送に対して、保存済みの請求書から同じ結果を組み立て直す。
//
// このユースケースが発行した請求書は必ず「未使用分」「残期間分」の 2 行を持つ。
// 接頭辞で名前空間を分けているので他の経路の請求書が来ることはないが、壊れたデータに
// 当たったときに index out of range で panic しないよう検査しておく。
func replayResult(
	ctx context.Context, uow UnitOfWork, cmd ChangePlanCommand, invoice *domain.Invoice,
) (ChangePlanResult, error) {
	if string(invoice.SubscriptionID) != cmd.SubscriptionID {
		return ChangePlanResult{}, fmt.Errorf(
			"%w: idempotency key %q was used for another subscription",
			ErrConflictingRequest, cmd.IdempotencyKey)
	}
	if len(invoice.Lines) != 2 {
		return ChangePlanResult{}, fmt.Errorf(
			"%w: invoice %q does not look like a plan change (expected 2 lines, found %d)",
			ErrConflictingRequest, invoice.ID, len(invoice.Lines))
	}

	subscription, err := uow.Subscriptions().Get(ctx, domain.SubscriptionID(cmd.SubscriptionID))
	if err != nil {
		return ChangePlanResult{}, err
	}
	if subscription == nil {
		return ChangePlanResult{}, NotFound("subscription", cmd.SubscriptionID)
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
		Invoice: view,
	}, nil
}

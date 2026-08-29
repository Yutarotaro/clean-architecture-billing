package usecase

import (
	"context"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
)

// CancelCommand は解約の要求。
type CancelCommand struct {
	SubscriptionID string
	Immediately    bool
}

// CancelSubscription は解約する。既定は期末解約。
//
// 外部システムを一切叩かないので、トランザクションは 1 つで済む。ユースケースが
// 全部同じ形をしている必要はない。必要な分だけ書く。
type CancelSubscription struct {
	factory UnitOfWorkFactory
	clock   Clock
}

// NewCancelSubscription は解約ユースケースを組み立てる。
func NewCancelSubscription(factory UnitOfWorkFactory, clock Clock) *CancelSubscription {
	return &CancelSubscription{factory: factory, clock: clock}
}

// Execute は解約する。
func (u *CancelSubscription) Execute(ctx context.Context, cmd CancelCommand) (SubscriptionView, error) {
	var view SubscriptionView
	err := inTransaction(ctx, u.factory, func(uow UnitOfWork) error {
		subscription, err := uow.Subscriptions().Get(ctx, domain.SubscriptionID(cmd.SubscriptionID))
		if err != nil {
			return err
		}
		if subscription == nil {
			return NotFound("subscription", cmd.SubscriptionID)
		}
		if err := subscription.Cancel(u.clock.Now(), cmd.Immediately); err != nil {
			return err
		}
		if err := uow.Subscriptions().Save(ctx, subscription); err != nil {
			return err
		}
		view = subscriptionView(subscription)
		return uow.Commit()
	})
	return view, err
}

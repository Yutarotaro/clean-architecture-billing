package usecase

import (
	"context"
	"fmt"
	"time"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
)

// RenewDueSubscriptions は請求期間が満了した契約を次の期間に進め、必要なら請求する。
//
// 契約 1 件ごとにトランザクションを切る。1 万件を 1 トランザクションでまとめて処理すると、
// 9,999 件目のデータ不整合で全部が巻き戻る。バッチは「途中で落ちても続きから再開できる」
// 形にしておくのが基本で、そのために冪等キーを期間の開始時刻から決定的に組み立てている。
type RenewDueSubscriptions struct {
	factory UnitOfWorkFactory
	clock   Clock
	ids     IDGenerator
	gateway PaymentGateway
}

// NewRenewDueSubscriptions は更新バッチを組み立てる。
func NewRenewDueSubscriptions(
	factory UnitOfWorkFactory, clock Clock, ids IDGenerator, gateway PaymentGateway,
) *RenewDueSubscriptions {
	return &RenewDueSubscriptions{factory: factory, clock: clock, ids: ids, gateway: gateway}
}

// Execute は期限の来た契約をまとめて更新する。
func (u *RenewDueSubscriptions) Execute(ctx context.Context, limit int) (RenewalReport, error) {
	now := u.clock.Now()

	var dueIDs []domain.SubscriptionID
	if err := inTransaction(ctx, u.factory, func(uow UnitOfWork) error {
		due, err := uow.Subscriptions().ListDue(ctx, now, limit)
		if err != nil {
			return err
		}
		for _, subscription := range due {
			dueIDs = append(dueIDs, subscription.ID)
		}
		return nil
	}); err != nil {
		return RenewalReport{}, err
	}

	report := RenewalReport{}
	for _, id := range dueIDs {
		invoiceID, terminated, err := u.renewOne(ctx, id)
		if err != nil {
			return RenewalReport{}, err
		}
		if terminated {
			report.Terminated++
			continue
		}
		report.Renewed++
		if invoiceID == "" {
			continue
		}
		report.Invoiced++
		outcome, err := chargeInvoice(ctx, u.factory, u.gateway, u.clock, invoiceID)
		if err != nil {
			return RenewalReport{}, err
		}
		if !outcome.Result.Succeeded {
			report.PaymentFailed++
		}
	}

	canceled, err := u.expirePastDue(ctx, limit)
	if err != nil {
		return RenewalReport{}, err
	}
	report.CanceledForNonpayment = canceled
	return report, nil
}

func (u *RenewDueSubscriptions) renewOne(
	ctx context.Context, id domain.SubscriptionID,
) (domain.InvoiceID, bool, error) {
	// 時刻はループの外で取った値ではなく毎回取り直す。バッチが数時間かかるとき、
	// 最初に取った now では「まだ満了していない」と判定される契約が出てくる。
	now := u.clock.Now()

	var (
		invoiceID  domain.InvoiceID
		terminated bool
	)
	err := inTransaction(ctx, u.factory, func(uow UnitOfWork) error {
		subscription, err := uow.Subscriptions().Get(ctx, id)
		if err != nil {
			return err
		}
		if subscription == nil {
			return NotFound("subscription", string(id))
		}
		plan, err := requirePlan(ctx, uow, subscription.PlanID)
		if err != nil {
			return err
		}

		needsCharge, err := subscription.Renew(*plan, now)
		if err != nil {
			return err
		}
		if err := uow.Subscriptions().Save(ctx, subscription); err != nil {
			return err
		}
		if !needsCharge {
			terminated = true
			return uow.Commit()
		}

		period := subscription.CurrentPeriod
		key := fmt.Sprintf("renew:%s:%s", subscription.ID, period.Start.Format(time.RFC3339Nano))
		existing, err := uow.Invoices().FindByIdempotencyKey(ctx, key)
		if err != nil {
			return err
		}
		if existing != nil {
			invoiceID = existing.ID
			return uow.Commit()
		}

		invoice, err := domain.IssueInvoice(
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
			key,
		)
		if err != nil {
			return err
		}
		if err := uow.Invoices().Add(ctx, invoice); err != nil {
			return err
		}
		invoiceID = invoice.ID
		return uow.Commit()
	})
	return invoiceID, terminated, err
}

func (u *RenewDueSubscriptions) expirePastDue(ctx context.Context, limit int) (int, error) {
	now := u.clock.Now()
	canceled := 0
	err := inTransaction(ctx, u.factory, func(uow UnitOfWork) error {
		pastDue, err := uow.Subscriptions().ListPastDue(ctx, limit)
		if err != nil {
			return err
		}
		for _, subscription := range pastDue {
			if subscription.ExpireIfGraceOver(now, domain.GracePeriod) {
				if err := uow.Subscriptions().Save(ctx, subscription); err != nil {
					return err
				}
				canceled++
			}
		}
		return uow.Commit()
	})
	return canceled, err
}

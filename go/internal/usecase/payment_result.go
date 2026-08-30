package usecase

import (
	"context"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
)

// PaymentNotification は決済代行から届く支払い結果の通知。
type PaymentNotification struct {
	InvoiceID         string
	Succeeded         bool
	ProviderReference string
	FailureReason     string
}

// RecordPaymentResult は決済代行から届いた支払い結果を取り込む。
//
// このユースケースは何度呼ばれても同じ結果にならなければならない。webhook は
// 「少なくとも 1 回」しか保証しない。同じ通知が 3 回届くのは異常ではなく通常の動作で、
// 二重処理を防ぐ責任は受け取る側にある。
type RecordPaymentResult struct {
	factory UnitOfWorkFactory
	clock   Clock
}

// NewRecordPaymentResult は決済結果取り込みユースケースを組み立てる。
func NewRecordPaymentResult(factory UnitOfWorkFactory, clock Clock) *RecordPaymentResult {
	return &RecordPaymentResult{factory: factory, clock: clock}
}

// Execute は支払い結果を反映する。
func (u *RecordPaymentResult) Execute(
	ctx context.Context, notification PaymentNotification,
) (InvoiceView, error) {
	var view InvoiceView
	err := inTransaction(ctx, u.factory, func(uow UnitOfWork) error {
		invoice, subscription, err := loadPair(ctx, uow, domain.InvoiceID(notification.InvoiceID))
		if err != nil {
			return err
		}
		now := u.clock.Now()

		switch {
		case notification.Succeeded:
			// 二度目以降は MarkPaid が何もしない。冪等性は「呼ぶ側が状態を確認する」
			// のではなく「呼ばれる側が守る」形にしてある。
			if err := invoice.MarkPaid(now); err != nil {
				return err
			}
			if err := subscription.MarkPaymentSucceeded(now); err != nil {
				return err
			}
		case invoice.IsSettled():
			// 決着済みの請求書に対する失敗通知は、順序が入れ替わって遅れて届いた
			// 古い通知である。webhook は配信順序を保証しないので、これは異常ではなく
			// 通常の動作。ここで past_due に落とすと、支払い済みの顧客が「未払い」として
			// 猶予期間ののちに解約される。
		default:
			if err := subscription.MarkPaymentFailed(now); err != nil {
				return err
			}
		}

		if err := uow.Invoices().Save(ctx, invoice); err != nil {
			return err
		}
		if err := uow.Subscriptions().Save(ctx, subscription); err != nil {
			return err
		}
		if view, err = invoiceView(invoice); err != nil {
			return err
		}
		return uow.Commit()
	})
	return view, err
}

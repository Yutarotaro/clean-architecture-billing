package usecase

import (
	"context"
	"errors"
	"time"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
)

// DefaultSettlementDelay より新しい請求書は掴まない。いま決済中かもしれないため。
const DefaultSettlementDelay = 15 * time.Minute

// SettleUnpaidInvoices は発行されたまま決着していない請求書に、もう一度決済を試みる。
//
// 決済 API の呼び出しはトランザクションの外で行っている（ADR-0005）。おかげで外部
// システムの遅さが DB の接続を占有しないが、そのかわり「請求書は発行できたが結果を
// 反映する前にプロセスが落ちる」窓が開く。決済代行との通信自体に失敗したときも同じ
// 状態になる。
//
// このユースケースがその窓を閉じる。請求書には冪等キーが付いているので、実は課金が
// 成功していた場合も決済代行の側で重複排除され、二重課金にはならない。
// この後始末が存在して初めて、ADR-0005 の判断が成立する。
type SettleUnpaidInvoices struct {
	factory UnitOfWorkFactory
	clock   Clock
	gateway PaymentGateway
}

// NewSettleUnpaidInvoices は拾い直しのユースケースを組み立てる。
func NewSettleUnpaidInvoices(
	factory UnitOfWorkFactory, clock Clock, gateway PaymentGateway,
) *SettleUnpaidInvoices {
	return &SettleUnpaidInvoices{factory: factory, clock: clock, gateway: gateway}
}

// Execute は決着していない請求書を拾い直す。olderThan が 0 なら既定値を使う。
func (u *SettleUnpaidInvoices) Execute(
	ctx context.Context, olderThan time.Duration, limit int,
) (SettlementReport, error) {
	if olderThan <= 0 {
		olderThan = DefaultSettlementDelay
	}
	cutoff := u.clock.Now().Add(-olderThan)

	var invoiceIDs []domain.InvoiceID
	if err := inTransaction(ctx, u.factory, func(uow UnitOfWork) error {
		unsettled, err := uow.Invoices().ListUnsettled(ctx, cutoff, limit)
		if err != nil {
			return err
		}
		for _, invoice := range unsettled {
			invoiceIDs = append(invoiceIDs, invoice.ID)
		}
		return nil
	}); err != nil {
		return SettlementReport{}, err
	}

	report := SettlementReport{}
	for _, invoiceID := range invoiceIDs {
		report.Examined++
		outcome, err := chargeInvoice(ctx, u.factory, u.gateway, u.clock, invoiceID)
		if err != nil {
			if !errors.Is(err, ErrPaymentGateway) {
				return SettlementReport{}, err
			}
			// まだ届かない。次の実行でまた拾われるので、ここでは数えるだけ。
			report.Unreachable++
			continue
		}
		if outcome.Result.Succeeded {
			report.Settled++
		} else {
			report.Declined++
		}
	}
	return report, nil
}

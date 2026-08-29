package usecase

import (
	"context"
	"fmt"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
)

// ChargeOutcome は決済とその反映の結果。
type ChargeOutcome struct {
	Result       PaymentResult
	Invoice      *domain.Invoice
	Subscription *domain.Subscription
}

// chargeInvoice は発行済みの請求書に対して決済を行い、結果を反映する。
//
// 決済 API の呼び出しをトランザクションの外に出しているのが要点である。DB の
// トランザクションを開いたまま外部 HTTP を叩くと、相手が 30 秒応答しないあいだ行ロックを
// 握り続けることになり、負荷が上がった瞬間に接続プールが枯渇する。
//
// そのかわり「請求書は発行できたが決済結果の反映前にプロセスが落ちる」窓が開く。
// 残された open な請求書はあとからバッチで拾い直せる形にしてあり、冪等キーがあるので
// 決済が実は成功していた場合も二重課金にはならない。
func chargeInvoice(
	ctx context.Context,
	factory UnitOfWorkFactory,
	gateway PaymentGateway,
	clock Clock,
	invoiceID domain.InvoiceID,
) (ChargeOutcome, error) {
	var req ChargeRequest

	if err := inTransaction(ctx, factory, func(uow UnitOfWork) error {
		invoice, subscription, err := loadPair(ctx, uow, invoiceID)
		if err != nil {
			return err
		}
		total, err := invoice.Total()
		if err != nil {
			return err
		}
		key := invoice.IdempotencyKey
		if key == "" {
			key = string(invoice.ID)
		}
		req = ChargeRequest{
			CustomerID:     invoice.CustomerID,
			Amount:         total,
			IdempotencyKey: key,
			Description:    fmt.Sprintf("subscription %s", subscription.ID),
		}
		return nil
	}); err != nil {
		return ChargeOutcome{}, err
	}

	result, err := gateway.Charge(ctx, req)
	if err != nil {
		return ChargeOutcome{}, fmt.Errorf("charge invoice %s: %w", invoiceID, err)
	}

	var outcome ChargeOutcome
	if err := inTransaction(ctx, factory, func(uow UnitOfWork) error {
		invoice, subscription, err := loadPair(ctx, uow, invoiceID)
		if err != nil {
			return err
		}
		now := clock.Now()
		if result.Succeeded {
			if err := invoice.MarkPaid(now); err != nil {
				return err
			}
			if err := subscription.MarkPaymentSucceeded(now); err != nil {
				return err
			}
		} else if err := subscription.MarkPaymentFailed(now); err != nil {
			return err
		}
		if err := uow.Invoices().Save(ctx, invoice); err != nil {
			return err
		}
		if err := uow.Subscriptions().Save(ctx, subscription); err != nil {
			return err
		}
		outcome = ChargeOutcome{Result: result, Invoice: invoice, Subscription: subscription}
		return uow.Commit()
	}); err != nil {
		return ChargeOutcome{}, err
	}
	return outcome, nil
}

func loadPair(
	ctx context.Context, uow UnitOfWork, invoiceID domain.InvoiceID,
) (*domain.Invoice, *domain.Subscription, error) {
	invoice, err := uow.Invoices().Get(ctx, invoiceID)
	if err != nil {
		return nil, nil, err
	}
	if invoice == nil {
		return nil, nil, NotFound("invoice", string(invoiceID))
	}
	subscription, err := uow.Subscriptions().Get(ctx, invoice.SubscriptionID)
	if err != nil {
		return nil, nil, err
	}
	if subscription == nil {
		return nil, nil, NotFound("subscription", string(invoice.SubscriptionID))
	}
	return invoice, subscription, nil
}

// inTransaction はトランザクションを開き、fn を実行し、必ず後始末をする。
//
// Python の with 文にあたるものを Go で書くとこうなる。defer で Rollback を予約して
// おけば、fn がどこで return しても、パニックしても、開きっぱなしにはならない。
// Commit 済みの Rollback は何もしない約束になっている。
func inTransaction(ctx context.Context, factory UnitOfWorkFactory, fn func(UnitOfWork) error) error {
	uow, err := factory(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = uow.Rollback() }()
	return fn(uow)
}

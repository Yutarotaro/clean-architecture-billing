package billingtest

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/payment"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/usecase"
)

func TestSubscribeWithoutTrialChargesImmediately(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)

		result := f.subscribe(t, "pro", "cus-1")

		if result.Subscription.Status != "active" {
			t.Errorf("Status = %s, want active", result.Subscription.Status)
		}
		if result.Invoice == nil {
			t.Fatal("Invoice = nil, want an invoice")
		}
		if result.Invoice.Total.Amount != 3_000 {
			t.Errorf("Total = %d, want 3000", result.Invoice.Total.Amount)
		}
		if result.Invoice.Status != "paid" {
			t.Errorf("invoice status = %s, want paid", result.Invoice.Status)
		}
		if got := len(f.gateway.Attempts()); got != 1 {
			t.Errorf("charge attempts = %d, want 1", got)
		}
	})
}

func TestSubscribeWithTrialDoesNotCharge(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)

		result, err := f.subscribeUC().Execute(context.Background(), usecase.SubscribeCommand{
			CustomerID: "cus-1", PlanID: "pro", TrialDays: 14,
		})
		if err != nil {
			t.Fatalf("Execute: %v", err)
		}

		if result.Subscription.Status != "trialing" {
			t.Errorf("Status = %s, want trialing", result.Subscription.Status)
		}
		if result.Invoice != nil {
			t.Errorf("Invoice = %+v, want nil during trial", result.Invoice)
		}
		if got := len(f.gateway.Attempts()); got != 0 {
			t.Errorf("charge attempts = %d, want 0", got)
		}
	})
}

// 決済が失敗しても契約自体は作られ、猶予期間に入る。
func TestDeclinedCardLeavesSubscriptionPastDue(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)
		f.gateway = payment.NewFakeDeclining(func(usecase.ChargeRequest) bool { return true })

		result, err := f.subscribeUC().Execute(context.Background(), usecase.SubscribeCommand{
			CustomerID: "cus-1", PlanID: "basic",
		})
		if err != nil {
			t.Fatalf("Execute: %v", err)
		}

		if !result.PaymentFailed {
			t.Error("PaymentFailed = false, want true")
		}
		if result.Subscription.Status != "past_due" {
			t.Errorf("Status = %s, want past_due", result.Subscription.Status)
		}
		if result.Invoice == nil || result.Invoice.Status != "open" {
			t.Errorf("invoice = %+v, want an open invoice", result.Invoice)
		}
	})
}

func TestSubscribeToUnknownPlanFails(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)

		_, err := f.subscribeUC().Execute(context.Background(), usecase.SubscribeCommand{
			CustomerID: "cus-1", PlanID: "nope",
		})
		if !errors.Is(err, usecase.ErrNotFound) {
			t.Errorf("Execute = %v, want ErrNotFound", err)
		}
	})
}

// 1 月の折り返しで Basic から Pro へ。差額 1,000 円が即時請求される。
func TestUpgradeMidPeriodChargesTheDifference(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)
		subscribed := f.subscribe(t, "basic", "cus-1")
		f.clock.Set(midJanuary)

		result, err := f.changePlanUC().Execute(context.Background(), usecase.ChangePlanCommand{
			SubscriptionID: subscribed.Subscription.ID,
			NewPlanID:      "pro",
			IdempotencyKey: "change-1",
		})
		if err != nil {
			t.Fatalf("Execute: %v", err)
		}

		if result.Subscription.PlanID != "pro" {
			t.Errorf("PlanID = %s, want pro", result.Subscription.PlanID)
		}
		if result.Proration.Credit.Amount != 500 {
			t.Errorf("Credit = %d, want 500", result.Proration.Credit.Amount)
		}
		if result.Proration.Charge.Amount != 1_500 {
			t.Errorf("Charge = %d, want 1500", result.Proration.Charge.Amount)
		}
		if result.Proration.Net.Amount != 1_000 {
			t.Errorf("Net = %d, want 1000", result.Proration.Net.Amount)
		}
		if result.Invoice == nil || result.Invoice.Status != "paid" {
			t.Fatalf("invoice = %+v, want a paid invoice", result.Invoice)
		}
		// 初回請求 1,000 円 + 差額 1,000 円
		if got := f.gateway.SettledAmount(); got != 2_000 {
			t.Errorf("settled = %d, want 2000", got)
		}
	})
}

// 合計だけでなく内訳を残す。問い合わせに答えられない請求書は不良品である。
func TestProrationInvoiceShowsBothSides(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)
		subscribed := f.subscribe(t, "basic", "cus-1")
		f.clock.Set(midJanuary)

		result, err := f.changePlanUC().Execute(context.Background(), usecase.ChangePlanCommand{
			SubscriptionID: subscribed.Subscription.ID,
			NewPlanID:      "pro",
			IdempotencyKey: "change-1",
		})
		if err != nil {
			t.Fatalf("Execute: %v", err)
		}
		if result.Invoice == nil || len(result.Invoice.Lines) != 2 {
			t.Fatalf("invoice = %+v, want 2 lines", result.Invoice)
		}
		if result.Invoice.Lines[0].Amount.Amount != -500 {
			t.Errorf("credit line = %d, want -500", result.Invoice.Lines[0].Amount.Amount)
		}
		if result.Invoice.Lines[1].Amount.Amount != 1_500 {
			t.Errorf("charge line = %d, want 1500", result.Invoice.Lines[1].Amount.Amount)
		}
	})
}

// 差額が負なら請求書は作らない。新しい料金は次の期間から効く。
func TestDowngradeDoesNotCharge(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)
		subscribed := f.subscribe(t, "pro", "cus-1")
		f.clock.Set(midJanuary)

		result, err := f.changePlanUC().Execute(context.Background(), usecase.ChangePlanCommand{
			SubscriptionID: subscribed.Subscription.ID,
			NewPlanID:      "basic",
			IdempotencyKey: "change-1",
		})
		if err != nil {
			t.Fatalf("Execute: %v", err)
		}

		if result.Proration.Net.Amount != -1_000 {
			t.Errorf("Net = %d, want -1000", result.Proration.Net.Amount)
		}
		if result.Invoice != nil {
			t.Errorf("Invoice = %+v, want nil for a downgrade", result.Invoice)
		}
		if got := f.gateway.SettledAmount(); got != 3_000 {
			t.Errorf("settled = %d, want 3000 (initial charge only)", got)
		}
	})
}

// ネットワークの再送で二重に課金しない。
func TestReplayingTheSameRequestDoesNotChargeTwice(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)
		subscribed := f.subscribe(t, "basic", "cus-1")
		f.clock.Set(midJanuary)
		cmd := usecase.ChangePlanCommand{
			SubscriptionID: subscribed.Subscription.ID,
			NewPlanID:      "pro",
			IdempotencyKey: "change-1",
		}

		first, err := f.changePlanUC().Execute(context.Background(), cmd)
		if err != nil {
			t.Fatalf("first: %v", err)
		}
		second, err := f.changePlanUC().Execute(context.Background(), cmd)
		if err != nil {
			t.Fatalf("second: %v", err)
		}

		if first.Invoice == nil || second.Invoice == nil {
			t.Fatal("both replays should return the invoice")
		}
		if first.Invoice.ID != second.Invoice.ID {
			t.Errorf("invoice ids differ: %s vs %s", first.Invoice.ID, second.Invoice.ID)
		}
		if second.Proration.Net.Amount != 1_000 {
			t.Errorf("replayed net = %d, want 1000", second.Proration.Net.Amount)
		}
		if got := f.gateway.SettledAmount(); got != 2_000 {
			t.Errorf("settled = %d, want 2000", got)
		}
	})
}

func TestReusingAKeyForAnotherSubscriptionIsRejected(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)
		first := f.subscribe(t, "basic", "cus-1")
		second := f.subscribe(t, "basic", "cus-2")
		f.clock.Set(midJanuary)

		if _, err := f.changePlanUC().Execute(context.Background(), usecase.ChangePlanCommand{
			SubscriptionID: first.Subscription.ID, NewPlanID: "pro", IdempotencyKey: "shared",
		}); err != nil {
			t.Fatalf("first change: %v", err)
		}

		_, err := f.changePlanUC().Execute(context.Background(), usecase.ChangePlanCommand{
			SubscriptionID: second.Subscription.ID, NewPlanID: "pro", IdempotencyKey: "shared",
		})
		if !errors.Is(err, usecase.ErrConflictingRequest) {
			t.Errorf("reused key = %v, want ErrConflictingRequest", err)
		}
	})
}

func TestCancelDefaultsToEndOfPeriod(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)
		subscribed := f.subscribe(t, "basic", "cus-1")

		view, err := f.cancelUC().Execute(context.Background(), usecase.CancelCommand{
			SubscriptionID: subscribed.Subscription.ID,
		})
		if err != nil {
			t.Fatalf("Execute: %v", err)
		}

		if view.Status != "active" {
			t.Errorf("Status = %s, want active until the period ends", view.Status)
		}
		if !view.CancelAtPeriodEnd {
			t.Error("CancelAtPeriodEnd = false, want true")
		}
	})
}

func TestPaymentWebhookIsIdempotent(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)
		f.gateway = payment.NewFakeDeclining(func(usecase.ChargeRequest) bool { return true })

		result, err := f.subscribeUC().Execute(context.Background(), usecase.SubscribeCommand{
			CustomerID: "cus-1", PlanID: "basic",
		})
		if err != nil {
			t.Fatalf("subscribe: %v", err)
		}
		if result.Invoice == nil {
			t.Fatal("expected an invoice")
		}
		notification := usecase.PaymentNotification{
			InvoiceID: result.Invoice.ID, Succeeded: true,
		}

		first, err := f.paymentUC().Execute(context.Background(), notification)
		if err != nil {
			t.Fatalf("first notification: %v", err)
		}
		f.clock.Advance(24 * time.Hour)
		second, err := f.paymentUC().Execute(context.Background(), notification)
		if err != nil {
			t.Fatalf("second notification: %v", err)
		}

		if first.Status != "paid" || second.Status != "paid" {
			t.Errorf("statuses = %s, %s; want paid, paid", first.Status, second.Status)
		}
		// 2 度目の通知で支払日時が書き換わってはいけない。
		if first.PaidAt == nil || second.PaidAt == nil || !first.PaidAt.Equal(*second.PaidAt) {
			t.Errorf("PaidAt changed on replay: %v vs %v", first.PaidAt, second.PaidAt)
		}

		view, err := f.queries().GetSubscription(context.Background(), result.Subscription.ID)
		if err != nil {
			t.Fatalf("GetSubscription: %v", err)
		}
		if view.Status != "active" {
			t.Errorf("Status = %s, want active after a late success", view.Status)
		}
	})
}

package billingtest

import (
	"context"
	"testing"
	"time"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/payment"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/usecase"
)

func TestRenewalAdvancesPeriodAndIssuesInvoice(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)
		subscribed := f.subscribe(t, "basic", "cus-1")
		f.clock.Set(feb)

		report, err := f.renewUC().Execute(context.Background(), 100)
		if err != nil {
			t.Fatalf("Execute: %v", err)
		}

		if report.Renewed != 1 || report.Invoiced != 1 || report.PaymentFailed != 0 {
			t.Errorf("report = %+v, want renewed=1 invoiced=1 failed=0", report)
		}

		view, err := f.queries().GetSubscription(context.Background(), subscribed.Subscription.ID)
		if err != nil {
			t.Fatalf("GetSubscription: %v", err)
		}
		if !view.CurrentPeriodStart.Equal(feb) || !view.CurrentPeriodEnd.Equal(mar) {
			t.Errorf("period = [%s, %s), want [feb, mar)",
				view.CurrentPeriodStart, view.CurrentPeriodEnd)
		}
		if got := f.gateway.SettledAmount(); got != 2_000 {
			t.Errorf("settled = %d, want 2000 (january + february)", got)
		}
	})
}

func TestRenewalIsNoOpBeforeThePeriodEnds(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)
		f.subscribe(t, "basic", "cus-1")
		f.clock.Set(jan.AddDate(0, 0, 19))

		report, err := f.renewUC().Execute(context.Background(), 100)
		if err != nil {
			t.Fatalf("Execute: %v", err)
		}
		if report.Renewed != 0 || report.Invoiced != 0 {
			t.Errorf("report = %+v, want all zeros", report)
		}
	})
}

// バッチが二重に起動されても、請求は 1 回で済む。
func TestRunningTheBatchTwiceDoesNotDoubleCharge(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)
		f.subscribe(t, "basic", "cus-1")
		f.clock.Set(feb)

		first, err := f.renewUC().Execute(context.Background(), 100)
		if err != nil {
			t.Fatalf("first run: %v", err)
		}
		second, err := f.renewUC().Execute(context.Background(), 100)
		if err != nil {
			t.Fatalf("second run: %v", err)
		}

		if first.Renewed != 1 {
			t.Errorf("first run renewed = %d, want 1", first.Renewed)
		}
		if second.Renewed != 0 {
			t.Errorf("second run renewed = %d, want 0", second.Renewed)
		}
		if got := f.gateway.SettledAmount(); got != 2_000 {
			t.Errorf("settled = %d, want 2000", got)
		}
	})
}

func TestScheduledCancellationTerminatesAtRenewal(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)
		subscribed := f.subscribe(t, "basic", "cus-1")
		if _, err := f.cancelUC().Execute(context.Background(), usecase.CancelCommand{
			SubscriptionID: subscribed.Subscription.ID,
		}); err != nil {
			t.Fatalf("cancel: %v", err)
		}
		f.clock.Set(feb)

		report, err := f.renewUC().Execute(context.Background(), 100)
		if err != nil {
			t.Fatalf("Execute: %v", err)
		}

		if report.Terminated != 1 || report.Invoiced != 0 {
			t.Errorf("report = %+v, want terminated=1 invoiced=0", report)
		}
		view, err := f.queries().GetSubscription(context.Background(), subscribed.Subscription.ID)
		if err != nil {
			t.Fatalf("GetSubscription: %v", err)
		}
		if view.Status != "canceled" {
			t.Errorf("Status = %s, want canceled", view.Status)
		}
		if got := f.gateway.SettledAmount(); got != 1_000 {
			t.Errorf("settled = %d, want 1000 (january only)", got)
		}
	})
}

func TestTrialConvertsIntoAPaidPeriod(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)
		result, err := f.subscribeUC().Execute(context.Background(), usecase.SubscribeCommand{
			CustomerID: "cus-1", PlanID: "pro", TrialDays: 14,
		})
		if err != nil {
			t.Fatalf("subscribe: %v", err)
		}
		f.clock.Set(jan.AddDate(0, 0, 14))

		report, err := f.renewUC().Execute(context.Background(), 100)
		if err != nil {
			t.Fatalf("Execute: %v", err)
		}

		if report.Invoiced != 1 {
			t.Errorf("invoiced = %d, want 1", report.Invoiced)
		}
		view, err := f.queries().GetSubscription(context.Background(), result.Subscription.ID)
		if err != nil {
			t.Fatalf("GetSubscription: %v", err)
		}
		if view.Status != "active" {
			t.Errorf("Status = %s, want active", view.Status)
		}
		if got := f.gateway.SettledAmount(); got != 3_000 {
			t.Errorf("settled = %d, want 3000", got)
		}
	})
}

// 支払い失敗 → 猶予 14 日 → 自動解約、までを時計だけで再現する。
func TestFailedRenewalGoesPastDueThenCancels(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)
		subscribed := f.subscribe(t, "basic", "cus-1")

		declining := payment.NewFakeDeclining(func(usecase.ChargeRequest) bool { return true })
		f.gateway = declining
		f.clock.Set(feb)

		report, err := f.renewUC().Execute(context.Background(), 100)
		if err != nil {
			t.Fatalf("renew: %v", err)
		}
		if report.PaymentFailed != 1 {
			t.Errorf("PaymentFailed = %d, want 1", report.PaymentFailed)
		}
		assertStatus(t, f, subscribed.Subscription.ID, "past_due")

		// 猶予期間の途中では解約されない。
		f.clock.Set(feb.AddDate(0, 0, 9))
		report, err = f.renewUC().Execute(context.Background(), 100)
		if err != nil {
			t.Fatalf("renew: %v", err)
		}
		if report.CanceledForNonpayment != 0 {
			t.Errorf("CanceledForNonpayment = %d, want 0 inside the grace period",
				report.CanceledForNonpayment)
		}
		assertStatus(t, f, subscribed.Subscription.ID, "past_due")

		// 14 日を過ぎると解約される。
		f.clock.Set(feb.Add(15 * 24 * time.Hour))
		report, err = f.renewUC().Execute(context.Background(), 100)
		if err != nil {
			t.Fatalf("renew: %v", err)
		}
		if report.CanceledForNonpayment != 1 {
			t.Errorf("CanceledForNonpayment = %d, want 1", report.CanceledForNonpayment)
		}
		assertStatus(t, f, subscribed.Subscription.ID, "canceled")
	})
}

// 複数件あっても、契約ごとにトランザクションが分かれている。
func TestBatchProcessesEachSubscriptionIndependently(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)
		var subscriptionIDs []string
		for _, customer := range []string{"cus-1", "cus-2", "cus-3"} {
			subscriptionIDs = append(subscriptionIDs, f.subscribe(t, "basic", customer).Subscription.ID)
		}
		f.clock.Set(feb)

		report, err := f.renewUC().Execute(context.Background(), 100)
		if err != nil {
			t.Fatalf("Execute: %v", err)
		}
		if report.Renewed != 3 {
			t.Errorf("Renewed = %d, want 3", report.Renewed)
		}
		for _, id := range subscriptionIDs {
			view, err := f.queries().GetSubscription(context.Background(), id)
			if err != nil {
				t.Fatalf("GetSubscription: %v", err)
			}
			if !view.CurrentPeriodStart.Equal(feb) {
				t.Errorf("%s period start = %s, want %s", id, view.CurrentPeriodStart, feb)
			}
		}
	})
}

func assertStatus(t *testing.T, f *fixture, subscriptionID, want string) {
	t.Helper()
	view, err := f.queries().GetSubscription(context.Background(), subscriptionID)
	if err != nil {
		t.Fatalf("GetSubscription: %v", err)
	}
	if view.Status != want {
		t.Errorf("Status = %s, want %s", view.Status, want)
	}
}

// 猶予切れの契約が複数あっても、1 件ずつ独立して解約される。
//
// まとめて 1 トランザクションにしていると、DynamoDB では 100 件で頭打ちになる。
// ユースケースの limit が永続化実装の制約と結びついてしまうため、
// バッチの粒度を 1 件に寄せてある。
func TestEveryPastDueSubscriptionIsExpiredIndependently(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)
		f.gateway = payment.NewFakeDeclining(func(usecase.ChargeRequest) bool { return true })

		var subscriptionIDs []string
		for _, customer := range []string{"cus-1", "cus-2", "cus-3"} {
			result, err := f.subscribeUC().Execute(context.Background(), usecase.SubscribeCommand{
				CustomerID: customer, PlanID: "basic",
			})
			if err != nil {
				t.Fatalf("subscribe: %v", err)
			}
			subscriptionIDs = append(subscriptionIDs, result.Subscription.ID)
			assertStatus(t, f, result.Subscription.ID, "past_due")
		}

		// 猶予 14 日を過ぎる。契約期間はまだ満了していないので、更新は走らない。
		f.clock.Set(jan.AddDate(0, 0, 15))
		report, err := f.renewUC().Execute(context.Background(), 100)
		if err != nil {
			t.Fatalf("Execute: %v", err)
		}

		if report.Renewed != 0 {
			t.Errorf("Renewed = %d, want 0", report.Renewed)
		}
		if report.CanceledForNonpayment != 3 {
			t.Errorf("CanceledForNonpayment = %d, want 3", report.CanceledForNonpayment)
		}
		for _, id := range subscriptionIDs {
			assertStatus(t, f, id, "canceled")
		}
	})
}

// 1 件の決済が通信失敗しても、残りの契約は処理される。
//
// ここでエラーが上まで抜けると被害が二重になる。この契約より後ろが処理されず、しかも
// 更新自体は commit 済みなので次回の実行では IsDue が偽になり、発行済みの請求書を
// 誰も拾わなくなる。契約は active のままなのでサービスは提供され続け、静かに売上が消える。
func TestAGatewayOutageDoesNotStopTheBatch(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)
		for _, customer := range []string{"cus-0", "cus-1", "cus-2"} {
			f.subscribe(t, "basic", customer)
		}

		// 更新のタイミングで、1 件だけ決済代行に届かなくする。
		f.gateway.SetFail(func(req usecase.ChargeRequest) bool {
			return req.CustomerID == "cus-0"
		})
		f.clock.Set(feb)

		report, err := f.renewUC().Execute(context.Background(), 100)
		if err != nil {
			t.Fatalf("Execute: %v", err)
		}

		if report.Renewed != 3 {
			t.Errorf("Renewed = %d, want 3", report.Renewed)
		}
		if report.Invoiced != 3 {
			t.Errorf("Invoiced = %d, want 3", report.Invoiced)
		}
		if report.ChargeUnreachable != 1 {
			t.Errorf("ChargeUnreachable = %d, want 1", report.ChargeUnreachable)
		}
		// 「届かなかった」を「拒否された」と混ぜない。混ぜると通信障害で顧客が解約される。
		if report.PaymentFailed != 0 {
			t.Errorf("PaymentFailed = %d, want 0", report.PaymentFailed)
		}
	})
}

// 通信失敗で取り残された請求書を、あとから拾い直せる。
//
// 決済 API の呼び出しをトランザクションの外に出している以上、結果を反映する前に
// 落ちる窓は必ず開く。この後始末が存在して初めて、その設計が成立する。
func TestUnsettledInvoicesAreSettledLater(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)
		subscribed := f.subscribe(t, "basic", "cus-1")

		f.gateway.SetFail(func(usecase.ChargeRequest) bool { return true })
		f.clock.Set(feb)
		report, err := f.renewUC().Execute(context.Background(), 100)
		if err != nil {
			t.Fatalf("renew: %v", err)
		}
		if report.ChargeUnreachable != 1 {
			t.Fatalf("ChargeUnreachable = %d, want 1", report.ChargeUnreachable)
		}
		assertInvoiceStatuses(t, f, "cus-1", []string{"paid", "open"})

		// 決済代行が復旧した。
		f.gateway.SetFail(nil)
		f.clock.Set(feb.Add(time.Hour))
		settlement, err := f.settleUC().Execute(context.Background(), 0, 100)
		if err != nil {
			t.Fatalf("settle: %v", err)
		}

		if settlement.Examined != 1 || settlement.Settled != 1 {
			t.Errorf("settlement = %+v, want examined=1 settled=1", settlement)
		}
		assertInvoiceStatuses(t, f, "cus-1", []string{"paid", "paid"})
		assertStatus(t, f, subscribed.Subscription.ID, "active")
		if got := f.gateway.SettledAmount(); got != 2_000 {
			t.Errorf("settled amount = %d, want 2000", got)
		}
	})
}

// 発行直後の請求書は掴まない。いま決済中かもしれない。
func TestSettlementIgnoresInvoicesThatMayStillBeInFlight(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		f := newFixture(t, factory)
		f.subscribe(t, "basic", "cus-1")

		f.gateway.SetFail(func(usecase.ChargeRequest) bool { return true })
		f.clock.Set(feb)
		if _, err := f.renewUC().Execute(context.Background(), 100); err != nil {
			t.Fatalf("renew: %v", err)
		}

		f.gateway.SetFail(nil)
		// まだ 15 分経っていない。
		f.clock.Set(feb.Add(5 * time.Minute))
		settlement, err := f.settleUC().Execute(context.Background(), 0, 100)
		if err != nil {
			t.Fatalf("settle: %v", err)
		}
		if settlement.Examined != 0 {
			t.Errorf("Examined = %d, want 0", settlement.Examined)
		}
	})
}

func assertInvoiceStatuses(t *testing.T, f *fixture, customerID string, want []string) {
	t.Helper()
	views, err := f.queries().ListInvoices(context.Background(), customerID)
	if err != nil {
		t.Fatalf("ListInvoices: %v", err)
	}
	got := make([]string, 0, len(views))
	for _, view := range views {
		got = append(got, view.Status)
	}
	if len(got) != len(want) {
		t.Fatalf("invoice statuses = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("invoice statuses = %v, want %v", got, want)
			return
		}
	}
}

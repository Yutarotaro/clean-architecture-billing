package billingtest

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/clock"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/ids"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/memory"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/payment"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/sqlite"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/usecase"
)

// 基準時刻。実時間に依存させない。
var (
	jan        = time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	feb        = time.Date(2026, 2, 1, 0, 0, 0, 0, time.UTC)
	mar        = time.Date(2026, 3, 1, 0, 0, 0, 0, time.UTC)
	midJanuary = time.Date(2026, 1, 16, 12, 0, 0, 0, time.UTC)
)

var (
	basicPlan = domain.Plan{
		ID: "basic", Name: "Basic", Price: domain.JPY(1_000), Interval: domain.IntervalMonthly,
	}
	proPlan = domain.Plan{
		ID: "pro", Name: "Pro", Price: domain.JPY(3_000), Interval: domain.IntervalMonthly,
	}
)

// backend は 1 つの永続化実装。
type backend struct {
	name    string
	factory usecase.UnitOfWorkFactory
}

// eachBackend は登録されたすべての実装に対して fn を実行する。
//
// テストを 1 つ書くだけで、実装の数だけ検証が走る。新しい実装を足したくなったら、
// まずこの関数に足してテストを緑にすればよい。
func eachBackend(t *testing.T, fn func(t *testing.T, factory usecase.UnitOfWorkFactory)) {
	t.Helper()
	for _, b := range backends(t) {
		t.Run(b.name, func(t *testing.T) {
			fn(t, b.factory)
		})
	}
}

func backends(t *testing.T) []backend {
	t.Helper()
	return []backend{
		{name: "memory", factory: memory.NewUnitOfWorkFactory(memory.NewDatabase())},
		{name: "sqlite", factory: newSQLiteFactory(t)},
	}
}

func newSQLiteFactory(t *testing.T) usecase.UnitOfWorkFactory {
	t.Helper()
	// :memory: ではなくファイルを使う。接続を共有すると「2 つのトランザクションが
	// 同時に走る」状況を再現できないため。
	dsn := "file:" + filepath.Join(t.TempDir(), "billing.db")
	db, err := sqlite.Open(dsn)
	if err != nil {
		t.Fatalf("sqlite.Open: %v", err)
	}
	t.Cleanup(func() { _ = db.Close() })
	if err := sqlite.CreateSchema(context.Background(), db); err != nil {
		t.Fatalf("sqlite.CreateSchema: %v", err)
	}
	return sqlite.NewUnitOfWorkFactory(db)
}

// seedPlans はテスト用のプランを投入する。
func seedPlans(t *testing.T, factory usecase.UnitOfWorkFactory) {
	t.Helper()
	ctx := context.Background()
	uow, err := factory(ctx)
	if err != nil {
		t.Fatalf("factory: %v", err)
	}
	defer func() { _ = uow.Rollback() }()

	for _, plan := range []domain.Plan{basicPlan, proPlan} {
		if err := uow.Plans().Add(ctx, plan); err != nil {
			t.Fatalf("Plans().Add: %v", err)
		}
	}
	if err := uow.Commit(); err != nil {
		t.Fatalf("Commit: %v", err)
	}
}

// fixture はユースケースを組み立てるための一式。
type fixture struct {
	factory usecase.UnitOfWorkFactory
	clock   *clock.Fixed
	ids     *ids.Sequential
	gateway *payment.Fake
}

func newFixture(t *testing.T, factory usecase.UnitOfWorkFactory) *fixture {
	t.Helper()
	seedPlans(t, factory)
	return &fixture{
		factory: factory,
		clock:   clock.NewFixed(jan),
		ids:     ids.NewSequential("id"),
		gateway: payment.NewFake(),
	}
}

func (f *fixture) subscribeUC() *usecase.SubscribeToPlan {
	return usecase.NewSubscribeToPlan(f.factory, f.clock, f.ids, f.gateway)
}

func (f *fixture) changePlanUC() *usecase.ChangePlan {
	return usecase.NewChangePlan(f.factory, f.clock, f.ids, f.gateway)
}

func (f *fixture) cancelUC() *usecase.CancelSubscription {
	return usecase.NewCancelSubscription(f.factory, f.clock)
}

func (f *fixture) paymentUC() *usecase.RecordPaymentResult {
	return usecase.NewRecordPaymentResult(f.factory, f.clock)
}

func (f *fixture) renewUC() *usecase.RenewDueSubscriptions {
	return usecase.NewRenewDueSubscriptions(f.factory, f.clock, f.ids, f.gateway)
}

func (f *fixture) settleUC() *usecase.SettleUnpaidInvoices {
	return usecase.NewSettleUnpaidInvoices(f.factory, f.clock, f.gateway)
}

func (f *fixture) queries() *usecase.Queries {
	return usecase.NewQueries(f.factory)
}

// subscribe はテストの前提として契約を 1 件作る。
func (f *fixture) subscribe(t *testing.T, planID, customerID string) usecase.SubscribeResult {
	t.Helper()
	result, err := f.subscribeUC().Execute(context.Background(), usecase.SubscribeCommand{
		CustomerID: customerID,
		PlanID:     planID,
	})
	if err != nil {
		t.Fatalf("subscribe: %v", err)
	}
	return result
}

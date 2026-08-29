package billingtest

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/storage"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/usecase"
)

func makeSubscription(t *testing.T, suffix string, at time.Time) *domain.Subscription {
	t.Helper()
	s, err := domain.Subscribe(
		domain.SubscriptionID("sub-"+suffix), "cus-1", basicPlan, at, 0)
	if err != nil {
		t.Fatalf("Subscribe: %v", err)
	}
	return s
}

func makeInvoice(t *testing.T, suffix, key string) *domain.Invoice {
	t.Helper()
	invoice, err := domain.IssueInvoice(
		domain.InvoiceID("inv-"+suffix), "cus-1", "sub-1",
		[]domain.InvoiceLine{{Description: "Basic", Amount: domain.JPY(1_000)}},
		"JPY", jan, key)
	if err != nil {
		t.Fatalf("IssueInvoice: %v", err)
	}
	return invoice
}

// withTx はトランザクションを 1 つ開いて fn を実行する。
func withTx(t *testing.T, factory usecase.UnitOfWorkFactory, fn func(usecase.UnitOfWork)) {
	t.Helper()
	uow, err := factory(context.Background())
	if err != nil {
		t.Fatalf("factory: %v", err)
	}
	defer func() { _ = uow.Rollback() }()
	fn(uow)
}

func TestPlansRoundTrip(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		seedPlans(t, factory)
		ctx := context.Background()

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			plan, err := uow.Plans().Get(ctx, "basic")
			if err != nil {
				t.Fatalf("Get: %v", err)
			}
			if plan == nil || *plan != basicPlan {
				t.Errorf("Get(basic) = %+v, want %+v", plan, basicPlan)
			}

			missing, err := uow.Plans().Get(ctx, "missing")
			if err != nil {
				t.Fatalf("Get(missing): %v", err)
			}
			if missing != nil {
				t.Errorf("Get(missing) = %+v, want nil", missing)
			}

			all, err := uow.Plans().ListAll(ctx)
			if err != nil {
				t.Fatalf("ListAll: %v", err)
			}
			if len(all) != 2 || all[0].ID != "basic" || all[1].ID != "pro" {
				t.Errorf("ListAll = %+v, want [basic pro]", all)
			}
		})
	})
}

// 保存して読み直したものが元と同じであること。値オブジェクトが列に分解され、また
// 組み立て直される経路が正しいかを見ている。時刻の情報が落ちていればここで落ちる。
func TestSubscriptionRoundTrip(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		seedPlans(t, factory)
		ctx := context.Background()
		original := makeSubscription(t, "1", jan)

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			if err := uow.Subscriptions().Add(ctx, original); err != nil {
				t.Fatalf("Add: %v", err)
			}
			if err := uow.Commit(); err != nil {
				t.Fatalf("Commit: %v", err)
			}
		})

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			loaded, err := uow.Subscriptions().Get(ctx, "sub-1")
			if err != nil {
				t.Fatalf("Get: %v", err)
			}
			if loaded == nil {
				t.Fatal("Get returned nil")
			}
			if loaded.ID != original.ID || loaded.Status != original.Status {
				t.Errorf("loaded = %+v, want %+v", loaded, original)
			}
			if !loaded.CurrentPeriod.Start.Equal(jan) || !loaded.CurrentPeriod.End.Equal(feb) {
				t.Errorf("period = [%s, %s), want [jan, feb)",
					loaded.CurrentPeriod.Start, loaded.CurrentPeriod.End)
			}
			if loaded.CurrentPeriod.Start.Location() != time.UTC {
				t.Error("stored timestamps must come back in UTC")
			}
		})
	})
}

// commit しなければ何も残らない。
func TestChangesAreInvisibleUntilCommit(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		seedPlans(t, factory)
		ctx := context.Background()

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			if err := uow.Subscriptions().Add(ctx, makeSubscription(t, "1", jan)); err != nil {
				t.Fatalf("Add: %v", err)
			}
			// Commit を呼ばずに抜ける
		})

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			found, err := uow.Subscriptions().Get(ctx, "sub-1")
			if err != nil {
				t.Fatalf("Get: %v", err)
			}
			if found != nil {
				t.Errorf("Get = %+v, want nil (uncommitted write leaked)", found)
			}
		})
	})
}

func TestExplicitRollbackDiscardsChanges(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		seedPlans(t, factory)
		ctx := context.Background()

		uow, err := factory(ctx)
		if err != nil {
			t.Fatalf("factory: %v", err)
		}
		if err := uow.Subscriptions().Add(ctx, makeSubscription(t, "1", jan)); err != nil {
			t.Fatalf("Add: %v", err)
		}
		if err := uow.Rollback(); err != nil {
			t.Fatalf("Rollback: %v", err)
		}

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			found, err := uow.Subscriptions().Get(ctx, "sub-1")
			if err != nil {
				t.Fatalf("Get: %v", err)
			}
			if found != nil {
				t.Errorf("Get = %+v, want nil after rollback", found)
			}
		})
	})
}

func TestSavingPersistsStateTransitions(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		seedPlans(t, factory)
		ctx := context.Background()

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			if err := uow.Subscriptions().Add(ctx, makeSubscription(t, "1", jan)); err != nil {
				t.Fatalf("Add: %v", err)
			}
			if err := uow.Commit(); err != nil {
				t.Fatalf("Commit: %v", err)
			}
		})

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			stored, err := uow.Subscriptions().Get(ctx, "sub-1")
			if err != nil {
				t.Fatalf("Get: %v", err)
			}
			if err := stored.Cancel(jan, true); err != nil {
				t.Fatalf("Cancel: %v", err)
			}
			if err := uow.Subscriptions().Save(ctx, stored); err != nil {
				t.Fatalf("Save: %v", err)
			}
			if err := uow.Commit(); err != nil {
				t.Fatalf("Commit: %v", err)
			}
		})

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			reloaded, err := uow.Subscriptions().Get(ctx, "sub-1")
			if err != nil {
				t.Fatalf("Get: %v", err)
			}
			if reloaded.Status != domain.StatusCanceled {
				t.Errorf("Status = %s, want canceled", reloaded.Status)
			}
			if reloaded.CanceledAt == nil || !reloaded.CanceledAt.Equal(jan) {
				t.Errorf("CanceledAt = %v, want %s", reloaded.CanceledAt, jan)
			}
		})
	})
}

func TestAddingTheSameIDTwiceIsRejected(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		seedPlans(t, factory)
		ctx := context.Background()

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			if err := uow.Subscriptions().Add(ctx, makeSubscription(t, "1", jan)); err != nil {
				t.Fatalf("Add: %v", err)
			}
			if err := uow.Commit(); err != nil {
				t.Fatalf("Commit: %v", err)
			}
		})

		// 重複は Add の時点か Commit の時点のどちらかで検出される。どちらになるかは
		// 実装によって違うため、契約としては「この区間のどこかで返る」と定める。
		withTx(t, factory, func(uow usecase.UnitOfWork) {
			err := uow.Subscriptions().Add(ctx, makeSubscription(t, "1", jan))
			if err == nil {
				err = uow.Commit()
			}
			if !errors.Is(err, storage.ErrDuplicate) {
				t.Errorf("duplicate add = %v, want storage.ErrDuplicate", err)
			}
		})
	})
}

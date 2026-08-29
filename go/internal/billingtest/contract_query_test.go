package billingtest

import (
	"context"
	"errors"
	"strconv"
	"testing"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/storage"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/usecase"
)

func TestListDueReturnsOnlyExpiredPeriods(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		seedPlans(t, factory)
		ctx := context.Background()

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			if err := uow.Subscriptions().Add(ctx, makeSubscription(t, "1", jan)); err != nil {
				t.Fatalf("Add: %v", err)
			}
			if err := uow.Subscriptions().Add(ctx, makeSubscription(t, "2", feb)); err != nil {
				t.Fatalf("Add: %v", err)
			}
			if err := uow.Commit(); err != nil {
				t.Fatalf("Commit: %v", err)
			}
		})

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			due, err := uow.Subscriptions().ListDue(ctx, feb, 100)
			if err != nil {
				t.Fatalf("ListDue: %v", err)
			}
			if len(due) != 1 || due[0].ID != "sub-1" {
				t.Errorf("ListDue = %v, want [sub-1]", idsOf(due))
			}
		})
	})
}

// 解約済みは二度と更新対象にならない。
func TestListDueExcludesCanceled(t *testing.T) {
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
			due, err := uow.Subscriptions().ListDue(ctx, feb, 100)
			if err != nil {
				t.Fatalf("ListDue: %v", err)
			}
			if len(due) != 0 {
				t.Errorf("ListDue = %v, want empty", idsOf(due))
			}
		})
	})
}

func TestListDueRespectsLimit(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		seedPlans(t, factory)
		ctx := context.Background()

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			for i := 0; i < 5; i++ {
				if err := uow.Subscriptions().Add(ctx, makeSubscription(t, strconv.Itoa(i), jan)); err != nil {
					t.Fatalf("Add: %v", err)
				}
			}
			if err := uow.Commit(); err != nil {
				t.Fatalf("Commit: %v", err)
			}
		})

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			due, err := uow.Subscriptions().ListDue(ctx, feb, 3)
			if err != nil {
				t.Fatalf("ListDue: %v", err)
			}
			if len(due) != 3 {
				t.Errorf("len(ListDue) = %d, want 3", len(due))
			}
		})
	})
}

func TestListPastDueFindsOnlyPastDue(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		seedPlans(t, factory)
		ctx := context.Background()

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			if err := uow.Subscriptions().Add(ctx, makeSubscription(t, "1", jan)); err != nil {
				t.Fatalf("Add: %v", err)
			}
			if err := uow.Subscriptions().Add(ctx, makeSubscription(t, "2", jan)); err != nil {
				t.Fatalf("Add: %v", err)
			}
			if err := uow.Commit(); err != nil {
				t.Fatalf("Commit: %v", err)
			}
		})

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			stored, err := uow.Subscriptions().Get(ctx, "sub-2")
			if err != nil {
				t.Fatalf("Get: %v", err)
			}
			if err := stored.MarkPaymentFailed(feb); err != nil {
				t.Fatalf("MarkPaymentFailed: %v", err)
			}
			if err := uow.Subscriptions().Save(ctx, stored); err != nil {
				t.Fatalf("Save: %v", err)
			}
			if err := uow.Commit(); err != nil {
				t.Fatalf("Commit: %v", err)
			}
		})

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			found, err := uow.Subscriptions().ListPastDue(ctx, 100)
			if err != nil {
				t.Fatalf("ListPastDue: %v", err)
			}
			if len(found) != 1 || found[0].ID != "sub-2" {
				t.Errorf("ListPastDue = %v, want [sub-2]", idsOf(found))
			}
		})
	})
}

func TestInvoiceRoundTripKeepsLineOrder(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		seedPlans(t, factory)
		ctx := context.Background()

		invoice, err := domain.IssueInvoice(
			"inv-1", "cus-1", "sub-1",
			[]domain.InvoiceLine{
				{Description: "Basic 未使用分", Amount: domain.JPY(-500)},
				{Description: "Pro 残期間分", Amount: domain.JPY(1_500)},
			},
			"JPY", jan, "")
		if err != nil {
			t.Fatalf("IssueInvoice: %v", err)
		}

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			if err := uow.Subscriptions().Add(ctx, makeSubscription(t, "1", jan)); err != nil {
				t.Fatalf("Add subscription: %v", err)
			}
			if err := uow.Invoices().Add(ctx, invoice); err != nil {
				t.Fatalf("Add invoice: %v", err)
			}
			if err := uow.Commit(); err != nil {
				t.Fatalf("Commit: %v", err)
			}
		})

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			loaded, err := uow.Invoices().Get(ctx, "inv-1")
			if err != nil {
				t.Fatalf("Get: %v", err)
			}
			if len(loaded.Lines) != 2 {
				t.Fatalf("len(Lines) = %d, want 2", len(loaded.Lines))
			}
			if loaded.Lines[0].Description != "Basic 未使用分" ||
				loaded.Lines[1].Description != "Pro 残期間分" {
				t.Errorf("lines out of order: %+v", loaded.Lines)
			}
			total, err := loaded.Total()
			if err != nil {
				t.Fatalf("Total: %v", err)
			}
			if total != domain.JPY(1_000) {
				t.Errorf("Total = %s, want 1000 JPY", total)
			}
		})
	})
}

func TestIdempotencyKeyLookup(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		seedPlans(t, factory)
		ctx := context.Background()

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			if err := uow.Subscriptions().Add(ctx, makeSubscription(t, "1", jan)); err != nil {
				t.Fatalf("Add: %v", err)
			}
			if err := uow.Invoices().Add(ctx, makeInvoice(t, "1", "renew:sub-1:2026-02-01")); err != nil {
				t.Fatalf("Add invoice: %v", err)
			}
			if err := uow.Commit(); err != nil {
				t.Fatalf("Commit: %v", err)
			}
		})

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			found, err := uow.Invoices().FindByIdempotencyKey(ctx, "renew:sub-1:2026-02-01")
			if err != nil {
				t.Fatalf("FindByIdempotencyKey: %v", err)
			}
			if found == nil || found.ID != "inv-1" {
				t.Errorf("FindByIdempotencyKey = %+v, want inv-1", found)
			}

			missing, err := uow.Invoices().FindByIdempotencyKey(ctx, "other")
			if err != nil {
				t.Fatalf("FindByIdempotencyKey: %v", err)
			}
			if missing != nil {
				t.Errorf("FindByIdempotencyKey(other) = %+v, want nil", missing)
			}
		})
	})
}

// 同じ鍵で 2 通目は作れない。二重請求を防ぐ最後の砦。
func TestIdempotencyKeyIsUnique(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		seedPlans(t, factory)
		ctx := context.Background()

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			if err := uow.Subscriptions().Add(ctx, makeSubscription(t, "1", jan)); err != nil {
				t.Fatalf("Add: %v", err)
			}
			if err := uow.Invoices().Add(ctx, makeInvoice(t, "1", "dup")); err != nil {
				t.Fatalf("Add invoice: %v", err)
			}
			if err := uow.Commit(); err != nil {
				t.Fatalf("Commit: %v", err)
			}
		})

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			err := uow.Invoices().Add(ctx, makeInvoice(t, "2", "dup"))
			if err == nil {
				err = uow.Commit()
			}
			if !errors.Is(err, storage.ErrDuplicate) {
				t.Errorf("duplicate key = %v, want storage.ErrDuplicate", err)
			}
		})
	})
}

func TestListInvoicesForCustomer(t *testing.T) {
	eachBackend(t, func(t *testing.T, factory usecase.UnitOfWorkFactory) {
		seedPlans(t, factory)
		ctx := context.Background()

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			if err := uow.Subscriptions().Add(ctx, makeSubscription(t, "1", jan)); err != nil {
				t.Fatalf("Add: %v", err)
			}
			if err := uow.Invoices().Add(ctx, makeInvoice(t, "1", "a")); err != nil {
				t.Fatalf("Add invoice: %v", err)
			}
			if err := uow.Invoices().Add(ctx, makeInvoice(t, "2", "b")); err != nil {
				t.Fatalf("Add invoice: %v", err)
			}
			if err := uow.Commit(); err != nil {
				t.Fatalf("Commit: %v", err)
			}
		})

		withTx(t, factory, func(uow usecase.UnitOfWork) {
			found, err := uow.Invoices().ListForCustomer(ctx, "cus-1")
			if err != nil {
				t.Fatalf("ListForCustomer: %v", err)
			}
			if len(found) != 2 {
				t.Errorf("len = %d, want 2", len(found))
			}

			none, err := uow.Invoices().ListForCustomer(ctx, "cus-999")
			if err != nil {
				t.Fatalf("ListForCustomer: %v", err)
			}
			if len(none) != 0 {
				t.Errorf("len = %d, want 0", len(none))
			}
		})
	})
}

// 先に読んだ側が後から書くと弾かれる（lost update の防止）。
func TestConcurrentUpdateIsDetected(t *testing.T) {
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

		outer, err := factory(ctx)
		if err != nil {
			t.Fatalf("factory: %v", err)
		}
		defer func() { _ = outer.Rollback() }()

		stale, err := outer.Subscriptions().Get(ctx, "sub-1")
		if err != nil {
			t.Fatalf("Get: %v", err)
		}

		// 別のトランザクションが先に更新する。
		withTx(t, factory, func(inner usecase.UnitOfWork) {
			fresh, err := inner.Subscriptions().Get(ctx, "sub-1")
			if err != nil {
				t.Fatalf("Get: %v", err)
			}
			if err := fresh.Cancel(jan, true); err != nil {
				t.Fatalf("Cancel: %v", err)
			}
			if err := inner.Subscriptions().Save(ctx, fresh); err != nil {
				t.Fatalf("Save: %v", err)
			}
			if err := inner.Commit(); err != nil {
				t.Fatalf("Commit: %v", err)
			}
		})

		if err := stale.Cancel(jan, false); err != nil {
			t.Fatalf("Cancel: %v", err)
		}
		// 衝突が Save で分かるか Commit で分かるかは実装によって異なる。
		// 呼び出し側は両方を見る必要がある、というのが契約である。
		saveErr := outer.Subscriptions().Save(ctx, stale)
		if saveErr == nil {
			saveErr = outer.Commit()
		}
		if !errors.Is(saveErr, usecase.ErrConcurrencyConflict) {
			t.Errorf("stale save = %v, want ErrConcurrencyConflict", saveErr)
		}
	})
}

// 読まずに save するのは、誰かの変更を無条件に上書きすることに等しい。
func TestSavingWithoutReadingIsRejected(t *testing.T) {
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
			err := uow.Subscriptions().Save(ctx, makeSubscription(t, "1", jan))
			if err == nil {
				err = uow.Commit()
			}
			if !errors.Is(err, usecase.ErrConcurrencyConflict) {
				t.Errorf("blind save = %v, want ErrConcurrencyConflict", err)
			}
		})
	})
}

func idsOf(subscriptions []*domain.Subscription) []domain.SubscriptionID {
	found := make([]domain.SubscriptionID, 0, len(subscriptions))
	for _, s := range subscriptions {
		found = append(found, s.ID)
	}
	return found
}

// Package memory はプロセス内に置く永続化実装を持つ。
//
// テストを速くするためだけのものではない。「リポジトリのインターフェースが本当に
// 永続化技術から独立しているか」を確かめる装置でもある。map で実装できないメソッドが
// 出てきたら、それは SQL の都合がインターフェースに漏れている証拠になる。
package memory

import (
	"sync"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
)

// Database はプロセス内の「データベース」。
type Database struct {
	mu            sync.RWMutex
	plans         map[domain.PlanID]domain.Plan
	subscriptions map[domain.SubscriptionID]*domain.Subscription
	invoices      map[domain.InvoiceID]*domain.Invoice
	// versions は楽観ロック用。SQL の version 列に相当する。
	versions map[string]int
}

// NewDatabase は空のデータベースを作る。
func NewDatabase() *Database {
	return &Database{
		plans:         map[domain.PlanID]domain.Plan{},
		subscriptions: map[domain.SubscriptionID]*domain.Subscription{},
		invoices:      map[domain.InvoiceID]*domain.Invoice{},
		versions:      map[string]int{},
	}
}

// cloneSubscription は集約の複製を作る。
//
// ポインタをそのまま返すと、呼び出し側がフィールドを書き換えた瞬間に、commit して
// いないのにデータベースの中身が変わる。トランザクションの意味が消えるので、
// 出し入れのたびに複製する。
func cloneSubscription(s *domain.Subscription) *domain.Subscription {
	if s == nil {
		return nil
	}
	clone := *s
	clone.PastDueSince = clonePtr(s.PastDueSince)
	clone.CanceledAt = clonePtr(s.CanceledAt)
	clone.TrialEnd = clonePtr(s.TrialEnd)
	return &clone
}

func cloneInvoice(i *domain.Invoice) *domain.Invoice {
	if i == nil {
		return nil
	}
	clone := *i
	clone.Lines = append([]domain.InvoiceLine(nil), i.Lines...)
	clone.IssuedAt = clonePtr(i.IssuedAt)
	clone.PaidAt = clonePtr(i.PaidAt)
	return &clone
}

func clonePtr[T any](p *T) *T {
	if p == nil {
		return nil
	}
	value := *p
	return &value
}

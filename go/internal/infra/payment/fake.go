// Package payment は usecase.PaymentGateway の実装を持つ。
package payment

import (
	"context"
	"fmt"
	"sync"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/usecase"
)

// Attempt は決済の試行 1 回ぶんの記録。
type Attempt struct {
	Request usecase.ChargeRequest
}

// Fake はテスト用の決済代行。
//
// モックではなく fake である。呼ばれた回数を検証するのではなく、本物と同じ規約
// （同じ冪等キーには同じ結果を返す）を実際に実装している。モックだと「二重課金しない」
// をテストしようとしても、モック自身が二重課金を許してしまう。
type Fake struct {
	mu          sync.Mutex
	declineWhen func(usecase.ChargeRequest) bool
	attempts    []Attempt
	results     map[string]usecase.PaymentResult
}

// NewFake は常に成功する決済代行を作る。
func NewFake() *Fake {
	return &Fake{results: map[string]usecase.PaymentResult{}}
}

// NewFakeDeclining は decline が true を返す要求を失敗させる決済代行を作る。
func NewFakeDeclining(decline func(usecase.ChargeRequest) bool) *Fake {
	return &Fake{declineWhen: decline, results: map[string]usecase.PaymentResult{}}
}

// SetDecline は失敗させる条件を差し替える。テストの途中で挙動を変えるために使う。
func (f *Fake) SetDecline(decline func(usecase.ChargeRequest) bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.declineWhen = decline
}

// Charge は課金する。同じ冪等キーの 2 回目以降は、新しく課金せず一度目の結果を返す。
func (f *Fake) Charge(_ context.Context, req usecase.ChargeRequest) (usecase.PaymentResult, error) {
	f.mu.Lock()
	defer f.mu.Unlock()

	f.attempts = append(f.attempts, Attempt{Request: req})

	if cached, ok := f.results[req.IdempotencyKey]; ok {
		// 本物の決済代行と同じ振る舞い。
		return cached, nil
	}

	var result usecase.PaymentResult
	if f.declineWhen != nil && f.declineWhen(req) {
		result = usecase.PaymentResult{Succeeded: false, FailureReason: "card_declined"}
	} else {
		result = usecase.PaymentResult{
			Succeeded:         true,
			ProviderReference: fmt.Sprintf("ch_%d", len(f.results)+1),
		}
	}
	f.results[req.IdempotencyKey] = result
	return result, nil
}

// Attempts は試行の記録を返す。
func (f *Fake) Attempts() []Attempt {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]Attempt(nil), f.attempts...)
}

// SettledAmount は実際に課金された合計を返す（冪等キーで重複排除したあと）。
func (f *Fake) SettledAmount() int64 {
	f.mu.Lock()
	defer f.mu.Unlock()

	seen := map[string]bool{}
	var total int64
	for _, attempt := range f.attempts {
		key := attempt.Request.IdempotencyKey
		if seen[key] {
			continue
		}
		seen[key] = true
		if f.results[key].Succeeded {
			total += attempt.Request.Amount.Amount
		}
	}
	return total
}

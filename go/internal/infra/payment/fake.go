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

// settled は一度決着した課金。冪等キーごとに 1 つだけ残る。
type settled struct {
	request usecase.ChargeRequest
	result  usecase.PaymentResult
}

// Fake はテスト用の決済代行。
//
// モックではなく fake である。呼ばれた回数を検証するのではなく、本物と同じ規約
// （同じ冪等キーには同じ結果を返す／同じ鍵で違うパラメータが来たら拒む）を実際に
// 実装している。モックだと「二重課金しない」をテストしようとしても、モック自身が
// 二重課金を許してしまう。
type Fake struct {
	mu          sync.Mutex
	declineWhen func(usecase.ChargeRequest) bool
	failWhen    func(usecase.ChargeRequest) bool
	attempts    []Attempt
	settled     map[string]settled
}

// NewFake は常に成功する決済代行を作る。
func NewFake() *Fake {
	return &Fake{settled: map[string]settled{}}
}

// NewFakeDeclining は decline が true を返す要求を「拒否」する決済代行を作る。
func NewFakeDeclining(decline func(usecase.ChargeRequest) bool) *Fake {
	return &Fake{declineWhen: decline, settled: map[string]settled{}}
}

// SetDecline は拒否する条件を差し替える。テストの途中で挙動を変えるために使う。
func (f *Fake) SetDecline(decline func(usecase.ChargeRequest) bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.declineWhen = decline
}

// SetFail は通信失敗させる条件を差し替える。
//
// 拒否（decline）と通信失敗（fail）は本物の決済代行でも扱いがまったく違う。
// 前者は未払いとして猶予期間に入れてよいが、後者は課金が成功している可能性がある。
func (f *Fake) SetFail(fail func(usecase.ChargeRequest) bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.failWhen = fail
}

// Charge は課金する。同じ冪等キーの 2 回目以降は、新しく課金せず一度目の結果を返す。
func (f *Fake) Charge(_ context.Context, req usecase.ChargeRequest) (usecase.PaymentResult, error) {
	f.mu.Lock()
	defer f.mu.Unlock()

	f.attempts = append(f.attempts, Attempt{Request: req})

	if f.failWhen != nil && f.failWhen(req) {
		return usecase.PaymentResult{}, fmt.Errorf(
			"%w: unreachable for %q", usecase.ErrPaymentGateway, req.IdempotencyKey)
	}

	if previous, ok := f.settled[req.IdempotencyKey]; ok {
		if previous.request.Amount != req.Amount ||
			previous.request.CustomerID != req.CustomerID {
			// 本物の決済代行は、同じ鍵に違うパラメータが来たら専用のエラーを返す。
			// ここを素通しにすると「同じ鍵で違う金額を請求してしまう」たぐいの不具合が
			// テストで検出できなくなる。冪等性を検証するための fake が、冪等性の誤用を隠す。
			return usecase.PaymentResult{}, fmt.Errorf(
				"%w: idempotency key %q was already used with different parameters "+
					"(was %s, now %s)",
				usecase.ErrPaymentGateway, req.IdempotencyKey,
				previous.request.Amount, req.Amount)
		}
		return previous.result, nil
	}

	var result usecase.PaymentResult
	if f.declineWhen != nil && f.declineWhen(req) {
		result = usecase.PaymentResult{Succeeded: false, FailureReason: "card_declined"}
	} else {
		result = usecase.PaymentResult{
			Succeeded:         true,
			ProviderReference: fmt.Sprintf("ch_%d", len(f.settled)+1),
		}
	}
	f.settled[req.IdempotencyKey] = settled{request: req, result: result}
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

	var total int64
	for _, s := range f.settled {
		if s.result.Succeeded {
			total += s.request.Amount.Amount
		}
	}
	return total
}

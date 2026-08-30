package usecase

import (
	"errors"
	"fmt"
)

// ユースケース層の失敗。ドメインの不変条件違反（domain.ErrInvariantViolation）とは
// 区別する。「その ID の契約が存在しない」はビジネスルールの違反ではなく、
// アプリケーションへの入力の問題である。
var (
	// ErrNotFound は対象の集約が存在しないことを表す。
	ErrNotFound = errors.New("entity not found")
	// ErrConflictingRequest は、同じ冪等キーで内容の違う要求が届いたことを表す。
	ErrConflictingRequest = errors.New("conflicting request")
	// ErrPaymentGateway は決済代行とのやりとり自体が失敗し、結果が分からないことを表す。
	//
	// 「カードが拒否された」は失敗ではなく PaymentResult{Succeeded: false} で表す。
	// そちらは決済代行がはっきり「駄目だ」と答えた状態であり、こちらは答えが返って
	// こなかった状態である。両者を混同すると、タイムアウトしただけの請求を「未払い」と
	// 判定して顧客を解約することになる。
	//
	// 結果が分からない以上、請求書は open のまま残す。実際には課金が成功している
	// かもしれないので、冪等キーを付けたうえで SettleUnpaidInvoices が拾い直す。
	ErrPaymentGateway = errors.New("payment gateway error")
	// ErrConcurrencyConflict は、同じ集約を別の実行が先に更新していたことを表す。
	//
	// どの永続化技術を使っていても起きうるのでインフラ層ではなくここに置く。
	// 技術固有の詳細（SQL の行数不一致、DynamoDB の ConditionalCheckFailed）は、
	// これを返す前に落としきる。
	ErrConcurrencyConflict = errors.New("concurrent modification")
)

// NotFound は「その ID のものがない」を表すエラーを作る。
func NotFound(entity, id string) error {
	return fmt.Errorf("%w: %s %q", ErrNotFound, entity, id)
}

// ConcurrencyConflict は楽観ロックの衝突を表すエラーを作る。
func ConcurrencyConflict(entity, id string) error {
	return fmt.Errorf("%w: %s %q was modified concurrently", ErrConcurrencyConflict, entity, id)
}

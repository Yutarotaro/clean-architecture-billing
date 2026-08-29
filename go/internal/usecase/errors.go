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

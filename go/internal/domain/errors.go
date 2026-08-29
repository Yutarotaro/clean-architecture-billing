package domain

import (
	"errors"
	"fmt"
)

// ドメインの失敗を表す番兵エラー。呼び出し側は errors.Is で判別し、
// HTTP のステータスコードへの変換は adapter 層が行う。
var (
	// ErrInvariantViolation は、生成時点で成り立つべき条件を満たしていないことを表す。
	ErrInvariantViolation = errors.New("domain invariant violated")
	// ErrIllegalTransition は、その状態からは許されない状態遷移を表す。
	ErrIllegalTransition = errors.New("illegal state transition")
	// ErrCurrencyMismatch は、異なる通貨どうしの計算を表す。
	ErrCurrencyMismatch = errors.New("currency mismatch")
)

// Invalid は不変条件違反を、理由つきで包んで返す。
func Invalid(format string, args ...any) error {
	return fmt.Errorf("%w: %s", ErrInvariantViolation, fmt.Sprintf(format, args...))
}

// IllegalTransition は、entity が state のときに action を行おうとしたことを表す。
func IllegalTransition(entity, state, action string) error {
	return fmt.Errorf("%w: cannot %s a %s in state %q", ErrIllegalTransition, action, entity, state)
}

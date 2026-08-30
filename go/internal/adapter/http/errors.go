package http

import (
	"errors"
	"net/http"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/usecase"
)

// statusFor はエラーを HTTP ステータスに翻訳する。
//
// この対応表がこの層にあることが重要である。domain.ErrIllegalTransition を返した
// ドメイン層は 409 という数字を知らないし、知る必要もない。同じエラーを gRPC で返すなら
// FAILED_PRECONDITION に、CLI なら終了コードに、それぞれの境界で好きに訳せばよい。
func statusFor(err error) (int, string) {
	switch {
	case errors.Is(err, usecase.ErrNotFound):
		return http.StatusNotFound, "not_found"
	case errors.Is(err, domain.ErrIllegalTransition):
		// 「解約済みの契約は変更できない」は入力の誤りではなく、資源の現在の状態と
		// 要求が両立しないということ。422 ではなく 409 が正しい。
		return http.StatusConflict, "illegal_state"
	case errors.Is(err, usecase.ErrConcurrencyConflict):
		return http.StatusConflict, "concurrent_modification"
	case errors.Is(err, usecase.ErrConflictingRequest):
		return http.StatusConflict, "conflicting_request"
	case errors.Is(err, usecase.ErrPaymentGateway):
		// 決済代行に届かなかった。こちらの落ち度ではないので 500 ではなく 502。
		// 課金できたかどうかは分からないため請求書は open のまま残っており、
		// SettleUnpaidInvoices が後から拾い直す。
		return http.StatusBadGateway, "payment_gateway_unreachable"
	case errors.Is(err, domain.ErrInvariantViolation),
		errors.Is(err, domain.ErrCurrencyMismatch):
		return http.StatusUnprocessableEntity, "domain_rule_violated"
	default:
		return http.StatusInternalServerError, "internal_error"
	}
}

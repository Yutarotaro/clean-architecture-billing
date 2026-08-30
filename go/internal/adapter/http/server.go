package http

import (
	"encoding/json"
	"log/slog"
	"net/http"
	"strconv"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/usecase"
)

// Handlers はこの API が必要とするユースケース一式。
//
// フレームワークの DI 機構に配線を書かず、構造体に受け取る。ユースケースの組み立ては
// 合成ルート（cmd/api）だけが知っていればよい。
type Handlers struct {
	Subscribe   *usecase.SubscribeToPlan
	ChangePlan  *usecase.ChangePlan
	Cancel      *usecase.CancelSubscription
	RecordPayme *usecase.RecordPaymentResult
	Renew       *usecase.RenewDueSubscriptions
	Queries     *usecase.Queries
	Logger      *slog.Logger
}

// NewServer はルーティングを組み立てる。
//
// HTTP フレームワークを入れていない。Go 1.22 の ServeMux はメソッドとパスパターンを
// 扱えるので、この規模ではこれで足りる。依存が 1 つ減れば、5 年後に動かなくなる
// 理由が 1 つ減る。
func NewServer(h Handlers) http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /health", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})

	mux.HandleFunc("GET /plans", h.listPlans)
	mux.HandleFunc("POST /subscriptions", h.subscribe)
	mux.HandleFunc("GET /subscriptions/{id}", h.getSubscription)
	mux.HandleFunc("POST /subscriptions/{id}/plan-changes", h.changePlan)
	mux.HandleFunc("POST /subscriptions/{id}/cancellation", h.cancel)
	mux.HandleFunc("GET /customers/{id}/invoices", h.listInvoices)
	mux.HandleFunc("POST /webhooks/payments", h.recordPayment)
	mux.HandleFunc("POST /admin/renewals", h.runRenewals)

	return mux
}

func (h Handlers) listPlans(w http.ResponseWriter, r *http.Request) {
	views, err := h.Queries.ListPlans(r.Context())
	if err != nil {
		h.writeError(w, err)
		return
	}
	plans := make([]planJSON, 0, len(views))
	for _, view := range views {
		plans = append(plans, toPlanJSON(view))
	}
	writeJSON(w, http.StatusOK, plans)
}

func (h Handlers) subscribe(w http.ResponseWriter, r *http.Request) {
	body, ok := decode[subscribeRequest](w, r, h)
	if !ok {
		return
	}
	result, err := h.Subscribe.Execute(r.Context(), usecase.SubscribeCommand{
		CustomerID: body.CustomerID,
		PlanID:     body.PlanID,
		TrialDays:  body.TrialDays,
	})
	if err != nil {
		h.writeError(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, subscribeResponse{
		Subscription:  toSubscriptionJSON(result.Subscription),
		Invoice:       toInvoiceJSONPtr(result.Invoice),
		PaymentFailed: result.PaymentFailed,
	})
}

func (h Handlers) getSubscription(w http.ResponseWriter, r *http.Request) {
	view, err := h.Queries.GetSubscription(r.Context(), r.PathValue("id"))
	if err != nil {
		h.writeError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, toSubscriptionJSON(view))
}

func (h Handlers) changePlan(w http.ResponseWriter, r *http.Request) {
	// 冪等キーを任意ではなく必須にしている。「再送しても安全な API」は、クライアントが
	// 気を利かせたときだけ成立する性質であってはならない。
	key := r.Header.Get("Idempotency-Key")
	if key == "" {
		writeJSON(w, http.StatusUnprocessableEntity, errorJSON{
			Error:  "missing_idempotency_key",
			Detail: "the Idempotency-Key header is required for plan changes",
		})
		return
	}
	body, ok := decode[changePlanRequest](w, r, h)
	if !ok {
		return
	}
	result, err := h.ChangePlan.Execute(r.Context(), usecase.ChangePlanCommand{
		SubscriptionID: r.PathValue("id"),
		NewPlanID:      body.NewPlanID,
		IdempotencyKey: key,
	})
	if err != nil {
		h.writeError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, changePlanResponse{
		Subscription: toSubscriptionJSON(result.Subscription),
		Proration:    toProrationJSON(result.Proration),
		Invoice:      toInvoiceJSON(result.Invoice),
	})
}

func (h Handlers) cancel(w http.ResponseWriter, r *http.Request) {
	body, ok := decode[cancelRequest](w, r, h)
	if !ok {
		return
	}
	view, err := h.Cancel.Execute(r.Context(), usecase.CancelCommand{
		SubscriptionID: r.PathValue("id"),
		Immediately:    body.Immediately,
	})
	if err != nil {
		h.writeError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, toSubscriptionJSON(view))
}

func (h Handlers) listInvoices(w http.ResponseWriter, r *http.Request) {
	views, err := h.Queries.ListInvoices(r.Context(), r.PathValue("id"))
	if err != nil {
		h.writeError(w, err)
		return
	}
	invoices := make([]invoiceJSON, 0, len(views))
	for _, view := range views {
		invoices = append(invoices, toInvoiceJSON(view))
	}
	writeJSON(w, http.StatusOK, invoices)
}

func (h Handlers) recordPayment(w http.ResponseWriter, r *http.Request) {
	// 実運用では、ここに署名検証（Stripe なら Stripe-Signature ヘッダ）が入る。
	// 「この HTTP リクエストが本物か」は境界の関心であって、ユースケースの関心ではない。
	body, ok := decode[paymentWebhookRequest](w, r, h)
	if !ok {
		return
	}
	view, err := h.RecordPayme.Execute(r.Context(), usecase.PaymentNotification{
		InvoiceID:         body.InvoiceID,
		Succeeded:         body.Succeeded,
		ProviderReference: body.ProviderReference,
		FailureReason:     body.FailureReason,
	})
	if err != nil {
		h.writeError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, toInvoiceJSON(view))
}

func (h Handlers) runRenewals(w http.ResponseWriter, r *http.Request) {
	limit := 100
	if raw := r.URL.Query().Get("limit"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed < 1 {
			writeJSON(w, http.StatusUnprocessableEntity, errorJSON{
				Error:  "invalid_limit",
				Detail: "limit must be a positive integer",
			})
			return
		}
		limit = parsed
	}
	report, err := h.Renew.Execute(r.Context(), limit)
	if err != nil {
		h.writeError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, renewalReportJSON{
		Renewed:               report.Renewed,
		Invoiced:              report.Invoiced,
		PaymentFailed:         report.PaymentFailed,
		Terminated:            report.Terminated,
		CanceledForNonpayment: report.CanceledForNonpayment,
	})
}

func decode[T any](w http.ResponseWriter, r *http.Request, h Handlers) (T, bool) {
	var body T
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		h.logger().Debug("malformed request body", "error", err)
		writeJSON(w, http.StatusBadRequest, errorJSON{
			Error:  "malformed_body",
			Detail: "request body is not valid JSON",
		})
		return body, false
	}
	return body, true
}

func (h Handlers) writeError(w http.ResponseWriter, err error) {
	status, code := statusFor(err)
	detail := err.Error()
	if status == http.StatusInternalServerError {
		// 予期しない失敗の中身はクライアントに返さない。DB の接続文字列やテーブル名が
		// エラーメッセージ経由で漏れるのはよくある事故である。ログには全部残す。
		h.logger().Error("unhandled error", "error", err)
		detail = "an unexpected error occurred"
	}
	writeJSON(w, status, errorJSON{Error: code, Detail: detail})
}

func (h Handlers) logger() *slog.Logger {
	if h.Logger != nil {
		return h.Logger
	}
	return slog.Default()
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(body); err != nil {
		// ヘッダは送信済みなので、ここでできるのは記録だけ。
		slog.Default().Error("cannot encode response", "error", err)
	}
}

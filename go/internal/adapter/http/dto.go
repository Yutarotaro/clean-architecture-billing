package http

import (
	"time"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/usecase"
)

// JSON の構造体はこのファイルの外に出さない。ユースケースは usecase パッケージの
// View だけを返し、それをここで 1 回変換する。手間を引き受ける代わりに、
// 「API の互換性のためにフィールド名を変えたい」がドメインに波及しなくなる。

type moneyJSON struct {
	Amount   int64  `json:"amount"`
	Currency string `json:"currency"`
}

func toMoneyJSON(v usecase.MoneyView) moneyJSON {
	return moneyJSON{Amount: v.Amount, Currency: v.Currency}
}

type planJSON struct {
	ID       string    `json:"id"`
	Name     string    `json:"name"`
	Price    moneyJSON `json:"price"`
	Interval string    `json:"interval"`
}

func toPlanJSON(v usecase.PlanView) planJSON {
	return planJSON{ID: v.ID, Name: v.Name, Price: toMoneyJSON(v.Price), Interval: v.Interval}
}

type subscriptionJSON struct {
	ID                 string     `json:"id"`
	CustomerID         string     `json:"customer_id"`
	PlanID             string     `json:"plan_id"`
	Status             string     `json:"status"`
	CurrentPeriodStart time.Time  `json:"current_period_start"`
	CurrentPeriodEnd   time.Time  `json:"current_period_end"`
	CancelAtPeriodEnd  bool       `json:"cancel_at_period_end"`
	TrialEnd           *time.Time `json:"trial_end"`
}

func toSubscriptionJSON(v usecase.SubscriptionView) subscriptionJSON {
	return subscriptionJSON{
		ID:                 v.ID,
		CustomerID:         v.CustomerID,
		PlanID:             v.PlanID,
		Status:             v.Status,
		CurrentPeriodStart: v.CurrentPeriodStart,
		CurrentPeriodEnd:   v.CurrentPeriodEnd,
		CancelAtPeriodEnd:  v.CancelAtPeriodEnd,
		TrialEnd:           v.TrialEnd,
	}
}

type invoiceLineJSON struct {
	Description string    `json:"description"`
	Amount      moneyJSON `json:"amount"`
}

type invoiceJSON struct {
	ID             string            `json:"id"`
	SubscriptionID string            `json:"subscription_id"`
	Status         string            `json:"status"`
	Total          moneyJSON         `json:"total"`
	Lines          []invoiceLineJSON `json:"lines"`
	IssuedAt       *time.Time        `json:"issued_at"`
	PaidAt         *time.Time        `json:"paid_at"`
}

func toInvoiceJSON(v usecase.InvoiceView) invoiceJSON {
	lines := make([]invoiceLineJSON, 0, len(v.Lines))
	for _, line := range v.Lines {
		lines = append(lines, invoiceLineJSON{
			Description: line.Description,
			Amount:      toMoneyJSON(line.Amount),
		})
	}
	return invoiceJSON{
		ID:             v.ID,
		SubscriptionID: v.SubscriptionID,
		Status:         v.Status,
		Total:          toMoneyJSON(v.Total),
		Lines:          lines,
		IssuedAt:       v.IssuedAt,
		PaidAt:         v.PaidAt,
	}
}

func toInvoiceJSONPtr(v *usecase.InvoiceView) *invoiceJSON {
	if v == nil {
		return nil
	}
	converted := toInvoiceJSON(*v)
	return &converted
}

type prorationJSON struct {
	Credit moneyJSON `json:"credit"`
	Charge moneyJSON `json:"charge"`
	Net    moneyJSON `json:"net"`
}

func toProrationJSON(v usecase.ProrationView) prorationJSON {
	return prorationJSON{
		Credit: toMoneyJSON(v.Credit),
		Charge: toMoneyJSON(v.Charge),
		Net:    toMoneyJSON(v.Net),
	}
}

type subscribeRequest struct {
	CustomerID string `json:"customer_id"`
	PlanID     string `json:"plan_id"`
	TrialDays  int    `json:"trial_days"`
}

type subscribeResponse struct {
	Subscription  subscriptionJSON `json:"subscription"`
	Invoice       *invoiceJSON     `json:"invoice"`
	PaymentFailed bool             `json:"payment_failed"`
}

type changePlanRequest struct {
	NewPlanID string `json:"new_plan_id"`
}

type changePlanResponse struct {
	Subscription subscriptionJSON `json:"subscription"`
	Proration    prorationJSON    `json:"proration"`
	// プラン変更は差額が 0 以下でも必ず請求書を残すので、常に存在する。
	Invoice invoiceJSON `json:"invoice"`
}

type cancelRequest struct {
	Immediately bool `json:"immediately"`
}

type paymentWebhookRequest struct {
	InvoiceID         string `json:"invoice_id"`
	Succeeded         bool   `json:"succeeded"`
	ProviderReference string `json:"provider_reference"`
	FailureReason     string `json:"failure_reason"`
}

type renewalReportJSON struct {
	Renewed               int `json:"renewed"`
	Invoiced              int `json:"invoiced"`
	PaymentFailed         int `json:"payment_failed"`
	Terminated            int `json:"terminated"`
	CanceledForNonpayment int `json:"canceled_for_nonpayment"`
}

type errorJSON struct {
	Error  string `json:"error"`
	Detail string `json:"detail"`
}

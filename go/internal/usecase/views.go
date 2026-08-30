package usecase

import (
	"time"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
)

// ユースケースの出力。adapter 層の JSON 構造体をここに持ち込まない。ユースケースは
// HTTP 経由でも CLI からでもバッチからでも呼ばれうるので、その形は「JSON にしやすい形」
// であってはならない。

// MoneyView は金額の表示用表現。
type MoneyView struct {
	Amount   int64
	Currency string
}

func moneyView(m domain.Money) MoneyView {
	return MoneyView{Amount: m.Amount, Currency: m.Currency}
}

// PlanView はプランの表示用表現。
type PlanView struct {
	ID       string
	Name     string
	Price    MoneyView
	Interval string
}

func planView(p domain.Plan) PlanView {
	return PlanView{
		ID:       string(p.ID),
		Name:     p.Name,
		Price:    moneyView(p.Price),
		Interval: string(p.Interval),
	}
}

// SubscriptionView は契約の表示用表現。
type SubscriptionView struct {
	ID                 string
	CustomerID         string
	PlanID             string
	Status             string
	CurrentPeriodStart time.Time
	CurrentPeriodEnd   time.Time
	CancelAtPeriodEnd  bool
	TrialEnd           *time.Time
}

func subscriptionView(s *domain.Subscription) SubscriptionView {
	return SubscriptionView{
		ID:                 string(s.ID),
		CustomerID:         string(s.CustomerID),
		PlanID:             string(s.PlanID),
		Status:             string(s.Status),
		CurrentPeriodStart: s.CurrentPeriod.Start,
		CurrentPeriodEnd:   s.CurrentPeriod.End,
		CancelAtPeriodEnd:  s.CancelAtPeriodEnd,
		TrialEnd:           s.TrialEnd,
	}
}

// InvoiceLineView は明細の表示用表現。
type InvoiceLineView struct {
	Description string
	Amount      MoneyView
}

// InvoiceView は請求書の表示用表現。
type InvoiceView struct {
	ID             string
	SubscriptionID string
	Status         string
	Total          MoneyView
	Lines          []InvoiceLineView
	IssuedAt       *time.Time
	PaidAt         *time.Time
}

func invoiceView(i *domain.Invoice) (InvoiceView, error) {
	total, err := i.Total()
	if err != nil {
		return InvoiceView{}, err
	}
	lines := make([]InvoiceLineView, 0, len(i.Lines))
	for _, line := range i.Lines {
		lines = append(lines, InvoiceLineView{
			Description: line.Description,
			Amount:      moneyView(line.Amount),
		})
	}
	return InvoiceView{
		ID:             string(i.ID),
		SubscriptionID: string(i.SubscriptionID),
		Status:         string(i.Status),
		Total:          moneyView(total),
		Lines:          lines,
		IssuedAt:       i.IssuedAt,
		PaidAt:         i.PaidAt,
	}, nil
}

// ProrationView は日割りの内訳の表示用表現。
type ProrationView struct {
	Credit MoneyView
	Charge MoneyView
	Net    MoneyView
}

func prorationView(p domain.Proration) (ProrationView, error) {
	net, err := p.Net()
	if err != nil {
		return ProrationView{}, err
	}
	return ProrationView{
		Credit: moneyView(p.Credit),
		Charge: moneyView(p.Charge),
		Net:    moneyView(net),
	}, nil
}

// RenewalReport はバッチ 1 回ぶんの結果。
type RenewalReport struct {
	Renewed  int
	Invoiced int
	// PaymentFailed は決済代行がはっきり拒否した件数。契約は past_due に落ちる。
	PaymentFailed int
	// ChargeUnreachable は決済代行に届かず、結果が分からなかった件数。
	//
	// 請求書は open のまま残り、SettleUnpaidInvoices が後から拾い直す。拒否と
	// 混ぜてしまうと、通信障害を「未払い」と誤判定して顧客を解約することになる。
	ChargeUnreachable     int
	Terminated            int
	CanceledForNonpayment int
}

// SettlementReport は決済結果の反映漏れを拾い直した結果。
type SettlementReport struct {
	Examined    int
	Settled     int
	Declined    int
	Unreachable int
}

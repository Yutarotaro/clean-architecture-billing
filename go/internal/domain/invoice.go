package domain

import "time"

// InvoiceStatus は請求書の状態。
type InvoiceStatus string

const (
	// InvoiceOpen は発行済みで支払い待ち。
	InvoiceOpen InvoiceStatus = "open"
	// InvoicePaid は支払い済み。
	InvoicePaid InvoiceStatus = "paid"
	// InvoiceUncollectible は回収不能として締めた状態。会計上は貸倒れ。
	InvoiceUncollectible InvoiceStatus = "uncollectible"
	// InvoiceVoid は誤発行として無効化した状態。
	InvoiceVoid InvoiceStatus = "void"
)

// InvoiceLine は請求書の明細 1 行。金額は負にもなりうる（返金・クレジット）。
type InvoiceLine struct {
	Description string
	Amount      Money
}

// Invoice は請求書 1 通を表す集約。
//
// Subscription とは別の集約にしている。発行済みの請求書は契約の現在の状態とは独立した
// 記録であり、契約を解約したからといって過去の請求書が消えては困る。ライフサイクルが
// 違うものは別の集約にする。
type Invoice struct {
	ID             InvoiceID
	CustomerID     CustomerID
	SubscriptionID SubscriptionID
	Lines          []InvoiceLine
	Currency       string
	Status         InvoiceStatus
	IssuedAt       *time.Time
	PaidAt         *time.Time
	// IdempotencyKey は同じ操作が二度届いたときに二重発行を防ぐための鍵。
	IdempotencyKey string
}

// IssueInvoice は請求書を発行する。
func IssueInvoice(
	id InvoiceID,
	customerID CustomerID,
	subscriptionID SubscriptionID,
	lines []InvoiceLine,
	currency string,
	at time.Time,
	idempotencyKey string,
) (*Invoice, error) {
	if len(lines) == 0 {
		return nil, Invalid("cannot issue an invoice with no lines")
	}
	for _, line := range lines {
		if line.Description == "" {
			return nil, Invalid("invoice line needs a description")
		}
		if line.Amount.Currency != currency {
			return nil, Invalid("line currency %s != invoice currency %s",
				line.Amount.Currency, currency)
		}
	}
	issued := EnsureUTC(at)
	return &Invoice{
		ID:             id,
		CustomerID:     customerID,
		SubscriptionID: subscriptionID,
		Lines:          append([]InvoiceLine(nil), lines...),
		Currency:       currency,
		Status:         InvoiceOpen,
		IssuedAt:       &issued,
		IdempotencyKey: idempotencyKey,
	}, nil
}

// Total は明細の合計。
func (i *Invoice) Total() (Money, error) {
	total := Money{Amount: 0, Currency: i.Currency}
	for _, line := range i.Lines {
		var err error
		if total, err = total.Add(line.Amount); err != nil {
			return Money{}, err
		}
	}
	return total, nil
}

// IsSettled は決着済みかを返す。
func (i *Invoice) IsSettled() bool {
	return i.Status == InvoicePaid || i.Status == InvoiceVoid || i.Status == InvoiceUncollectible
}

// MarkPaid は支払い済みにする。
//
// すでに支払い済みなら何もしない。決済プロバイダの webhook は同じ通知を複数回送って
// くるので、二度目を error にすると向こうは「失敗した」と見なして延々と再送してくる。
func (i *Invoice) MarkPaid(at time.Time) error {
	if i.Status == InvoicePaid {
		return nil
	}
	if i.Status != InvoiceOpen {
		return IllegalTransition("invoice", string(i.Status), "pay")
	}
	paid := EnsureUTC(at)
	i.Status = InvoicePaid
	i.PaidAt = &paid
	return nil
}

// MarkUncollectible は回収不能として締める。
func (i *Invoice) MarkUncollectible() error {
	if i.Status != InvoiceOpen {
		return IllegalTransition("invoice", string(i.Status), "write off")
	}
	i.Status = InvoiceUncollectible
	return nil
}

// Void は無効化する。
func (i *Invoice) Void() error {
	if i.Status != InvoiceOpen {
		return IllegalTransition("invoice", string(i.Status), "void")
	}
	i.Status = InvoiceVoid
	return nil
}

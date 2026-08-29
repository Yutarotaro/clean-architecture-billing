package sqlite

import (
	"database/sql"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
	"github.com/Yutarotaro/clean-architecture-billing/go/internal/infra/storage"
)

// subscriptionRow はテーブルの 1 行をそのまま写した構造体。
//
// ドメインの Subscription とは別の型にしている。ここを共用すると、列を 1 つ足すたびに
// ドメインモデルが引きずられる。変換のコードは退屈だが、その退屈さが境界を守っている。
type subscriptionRow struct {
	id                string
	customerID        string
	planID            string
	status            string
	periodStart       string
	periodEnd         string
	cancelAtPeriodEnd bool
	pastDueSince      sql.NullString
	canceledAt        sql.NullString
	trialEnd          sql.NullString
	version           int
}

func (r subscriptionRow) toDomain() (*domain.Subscription, error) {
	start, err := storage.ParseTime(r.periodStart)
	if err != nil {
		return nil, err
	}
	end, err := storage.ParseTime(r.periodEnd)
	if err != nil {
		return nil, err
	}
	period, err := domain.NewBillingPeriod(start, end)
	if err != nil {
		return nil, err
	}
	pastDueSince, err := storage.ParseTimePtr(nullableString(r.pastDueSince))
	if err != nil {
		return nil, err
	}
	canceledAt, err := storage.ParseTimePtr(nullableString(r.canceledAt))
	if err != nil {
		return nil, err
	}
	trialEnd, err := storage.ParseTimePtr(nullableString(r.trialEnd))
	if err != nil {
		return nil, err
	}
	return &domain.Subscription{
		ID:                domain.SubscriptionID(r.id),
		CustomerID:        domain.CustomerID(r.customerID),
		PlanID:            domain.PlanID(r.planID),
		Status:            domain.SubscriptionStatus(r.status),
		CurrentPeriod:     period,
		CancelAtPeriodEnd: r.cancelAtPeriodEnd,
		PastDueSince:      pastDueSince,
		CanceledAt:        canceledAt,
		TrialEnd:          trialEnd,
	}, nil
}

func subscriptionArgs(s *domain.Subscription) []any {
	return []any{
		string(s.ID),
		string(s.CustomerID),
		string(s.PlanID),
		string(s.Status),
		storage.FormatTime(s.CurrentPeriod.Start),
		storage.FormatTime(s.CurrentPeriod.End),
		s.CancelAtPeriodEnd,
		storage.FormatTimePtr(s.PastDueSince),
		storage.FormatTimePtr(s.CanceledAt),
		storage.FormatTimePtr(s.TrialEnd),
	}
}

type invoiceRow struct {
	id             string
	customerID     string
	subscriptionID string
	status         string
	currency       string
	issuedAt       sql.NullString
	paidAt         sql.NullString
	idempotencyKey sql.NullString
	version        int
}

func (r invoiceRow) toDomain(lines []domain.InvoiceLine) (*domain.Invoice, error) {
	issuedAt, err := storage.ParseTimePtr(nullableString(r.issuedAt))
	if err != nil {
		return nil, err
	}
	paidAt, err := storage.ParseTimePtr(nullableString(r.paidAt))
	if err != nil {
		return nil, err
	}
	key := ""
	if r.idempotencyKey.Valid {
		key = r.idempotencyKey.String
	}
	return &domain.Invoice{
		ID:             domain.InvoiceID(r.id),
		CustomerID:     domain.CustomerID(r.customerID),
		SubscriptionID: domain.SubscriptionID(r.subscriptionID),
		Lines:          lines,
		Currency:       r.currency,
		Status:         domain.InvoiceStatus(r.status),
		IssuedAt:       issuedAt,
		PaidAt:         paidAt,
		IdempotencyKey: key,
	}, nil
}

func invoiceArgs(i *domain.Invoice) []any {
	var key any
	if i.IdempotencyKey != "" {
		key = i.IdempotencyKey
	}
	return []any{
		string(i.ID),
		string(i.CustomerID),
		string(i.SubscriptionID),
		string(i.Status),
		i.Currency,
		storage.FormatTimePtr(i.IssuedAt),
		storage.FormatTimePtr(i.PaidAt),
		key,
	}
}

func nullableString(value sql.NullString) *string {
	if !value.Valid {
		return nil
	}
	return &value.String
}

package domain

import "time"

// BillingInterval は課金サイクル。
type BillingInterval string

const (
	IntervalMonthly BillingInterval = "monthly"
	IntervalYearly  BillingInterval = "yearly"
)

// NextAfter は start を起点とした次の請求日を返す。
func (i BillingInterval) NextAfter(start time.Time) (time.Time, error) {
	switch i {
	case IntervalMonthly:
		return AddMonths(start, 1), nil
	case IntervalYearly:
		return AddMonths(start, 12), nil
	default:
		return time.Time{}, Invalid("unknown billing interval %q", string(i))
	}
}

// Plan は契約できる料金プラン。不変。
//
// 価格改定は「既存プランの Price を書き換える」のではなく「新しい Plan を作って以後の
// 契約をそちらに向ける」と考える。発行済みの請求書の金額が、後からプランを編集した
// せいで変わる事故を構造的に防ぐ。
type Plan struct {
	ID       PlanID
	Name     string
	Price    Money
	Interval BillingInterval
}

// NewPlan はプランを作る。
func NewPlan(id PlanID, name string, price Money, interval BillingInterval) (Plan, error) {
	if name == "" {
		return Plan{}, Invalid("plan name must not be empty")
	}
	if price.IsNegative() {
		return Plan{}, Invalid("plan price must not be negative, got %s", price)
	}
	if _, err := interval.NextAfter(time.Now()); err != nil {
		return Plan{}, err
	}
	return Plan{ID: id, Name: name, Price: price, Interval: interval}, nil
}

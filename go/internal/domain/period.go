package domain

import (
	"math/big"
	"time"
)

// BillingPeriod は [Start, End) の半開区間。終端は含まない。
//
// 半開区間にしておくと、ある期間の End と次の期間の Start が同じ値になり、隙間も
// 重複も生まれない。「23:59:59 まで」と書くと、必ずどこかに穴が空く。
type BillingPeriod struct {
	Start time.Time
	End   time.Time
}

// NewBillingPeriod は期間を作る。
func NewBillingPeriod(start, end time.Time) (BillingPeriod, error) {
	start, end = EnsureUTC(start), EnsureUTC(end)
	if !start.Before(end) {
		return BillingPeriod{}, Invalid("period must be non-empty: %s >= %s",
			start.Format(time.RFC3339), end.Format(time.RFC3339))
	}
	return BillingPeriod{Start: start, End: end}, nil
}

// PeriodStartingAt は interval 1 つぶんの期間を作る。
func PeriodStartingAt(start time.Time, interval BillingInterval) (BillingPeriod, error) {
	end, err := interval.NextAfter(EnsureUTC(start))
	if err != nil {
		return BillingPeriod{}, err
	}
	return NewBillingPeriod(start, end)
}

// Contains は at がこの期間に含まれるかを返す。終端は含まない。
func (p BillingPeriod) Contains(at time.Time) bool {
	at = EnsureUTC(at)
	return !at.Before(p.Start) && at.Before(p.End)
}

// IsDue は at の時点でこの期間が満了しているかを返す。
func (p BillingPeriod) IsDue(at time.Time) bool {
	return !EnsureUTC(at).Before(p.End)
}

// RemainingRatio は at の時点で残っている期間の割合を厳密な分数で返す。
//
// float64 を経由しない。1/3 のような比率を float にすると、掛けたあとの丸めが
// 処理系依存になる。
func (p BillingPeriod) RemainingRatio(at time.Time) *big.Rat {
	at = EnsureUTC(at)
	switch {
	case !at.After(p.Start):
		return big.NewRat(1, 1)
	case !at.Before(p.End):
		return new(big.Rat)
	default:
		return big.NewRat(int64(p.End.Sub(at)), int64(p.End.Sub(p.Start)))
	}
}

package domain_test

import (
	"errors"
	"math/big"
	"testing"
	"time"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
)

var (
	jan = time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	feb = time.Date(2026, 2, 1, 0, 0, 0, 0, time.UTC)
	// 31 日間のちょうど半分（15.5 日）が経過した時点。
	midJanuary = time.Date(2026, 1, 16, 12, 0, 0, 0, time.UTC)
)

func mustPeriod(t *testing.T, start, end time.Time) domain.BillingPeriod {
	t.Helper()
	period, err := domain.NewBillingPeriod(start, end)
	if err != nil {
		t.Fatalf("NewBillingPeriod: %v", err)
	}
	return period
}

func TestPeriodMustBeNonEmpty(t *testing.T) {
	if _, err := domain.NewBillingPeriod(feb, jan); !errors.Is(err, domain.ErrInvariantViolation) {
		t.Errorf("NewBillingPeriod(feb, jan) = %v, want ErrInvariantViolation", err)
	}
}

// 終端は含まない。次の期間の開始と重ならないようにするため。
func TestPeriodIsHalfOpen(t *testing.T) {
	period := mustPeriod(t, jan, feb)

	if !period.Contains(jan) {
		t.Error("period should contain its start")
	}
	if period.Contains(feb) {
		t.Error("period should not contain its end")
	}
	if !period.IsDue(feb) {
		t.Error("period should be due at its end")
	}
	if period.IsDue(feb.Add(-time.Microsecond)) {
		t.Error("period should not be due just before its end")
	}
}

func TestRemainingRatio(t *testing.T) {
	period := mustPeriod(t, jan, feb)
	tests := []struct {
		name string
		at   time.Time
		want *big.Rat
	}{
		{"at start", jan, big.NewRat(1, 1)},
		{"halfway", midJanuary, big.NewRat(1, 2)},
		{"at end", feb, new(big.Rat)},
		{"after end", feb.AddDate(0, 0, 10), new(big.Rat)},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := period.RemainingRatio(tt.at); got.Cmp(tt.want) != 0 {
				t.Errorf("RemainingRatio = %s, want %s", got, tt.want)
			}
		})
	}
}

// 1 月の折り返し地点で 1,000 円プランから 3,000 円プランへ変えた場合。
// 残り半分なので、旧プランの 500 円を返し、新プランの 1,500 円を請求する。
func TestProrate(t *testing.T) {
	proration, err := domain.Prorate(
		mustPeriod(t, jan, feb), midJanuary, domain.JPY(1_000), domain.JPY(3_000))
	if err != nil {
		t.Fatalf("Prorate: %v", err)
	}

	if proration.Credit != domain.JPY(500) {
		t.Errorf("Credit = %s, want 500 JPY", proration.Credit)
	}
	if proration.Charge != domain.JPY(1_500) {
		t.Errorf("Charge = %s, want 1500 JPY", proration.Charge)
	}
	net, err := proration.Net()
	if err != nil {
		t.Fatalf("Net: %v", err)
	}
	if net != domain.JPY(1_000) {
		t.Errorf("Net = %s, want 1000 JPY", net)
	}
}

func TestProrateDowngradeIsNegative(t *testing.T) {
	proration, err := domain.Prorate(
		mustPeriod(t, jan, feb), midJanuary, domain.JPY(3_000), domain.JPY(1_000))
	if err != nil {
		t.Fatalf("Prorate: %v", err)
	}
	net, err := proration.Net()
	if err != nil {
		t.Fatalf("Net: %v", err)
	}
	if net != domain.JPY(-1_000) {
		t.Errorf("Net = %s, want -1000 JPY", net)
	}
}

// 1/31 の 1 か月後は 2/28。存在しない日付を作らないための丸め。
func TestAddMonthsClampsToEndOfMonth(t *testing.T) {
	tests := []struct {
		name   string
		start  time.Time
		months int
		want   time.Time
	}{
		{"jan 31 + 1", time.Date(2026, 1, 31, 0, 0, 0, 0, time.UTC), 1,
			time.Date(2026, 2, 28, 0, 0, 0, 0, time.UTC)},
		{"leap year", time.Date(2024, 1, 31, 0, 0, 0, 0, time.UTC), 1,
			time.Date(2024, 2, 29, 0, 0, 0, 0, time.UTC)},
		{"a year later", time.Date(2026, 1, 31, 0, 0, 0, 0, time.UTC), 12,
			time.Date(2027, 1, 31, 0, 0, 0, 0, time.UTC)},
		{"across year boundary", time.Date(2026, 12, 15, 0, 0, 0, 0, time.UTC), 1,
			time.Date(2027, 1, 15, 0, 0, 0, 0, time.UTC)},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := domain.AddMonths(tt.start, tt.months); !got.Equal(tt.want) {
				t.Errorf("AddMonths = %s, want %s", got, tt.want)
			}
		})
	}
}

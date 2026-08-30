package domain_test

import (
	"errors"
	"math/big"
	"testing"

	"github.com/Yutarotaro/clean-architecture-billing/go/internal/domain"
)

func TestMoneyArithmetic(t *testing.T) {
	sum, err := domain.JPY(1_000).Add(domain.JPY(500))
	if err != nil {
		t.Fatalf("Add: %v", err)
	}
	if sum != domain.JPY(1_500) {
		t.Errorf("Add = %s, want 1500 JPY", sum)
	}

	diff, err := domain.JPY(1_000).Sub(domain.JPY(1_500))
	if err != nil {
		t.Fatalf("Sub: %v", err)
	}
	if diff != domain.JPY(-500) {
		t.Errorf("Sub = %s, want -500 JPY", diff)
	}
}

func TestMoneyRejectsMixedCurrencies(t *testing.T) {
	usd, err := domain.NewMoney(10, "USD")
	if err != nil {
		t.Fatalf("NewMoney: %v", err)
	}
	if _, err := domain.JPY(1_000).Add(usd); !errors.Is(err, domain.ErrCurrencyMismatch) {
		t.Errorf("Add across currencies = %v, want ErrCurrencyMismatch", err)
	}
}

func TestNewMoneyValidatesCurrency(t *testing.T) {
	for _, currency := range []string{"jpy", "JPYEN", "J", ""} {
		if _, err := domain.NewMoney(100, currency); !errors.Is(err, domain.ErrInvariantViolation) {
			t.Errorf("NewMoney(%q) = %v, want ErrInvariantViolation", currency, err)
		}
	}
}

func TestMoneyScaleFloors(t *testing.T) {
	tests := []struct {
		name   string
		amount int64
		ratio  *big.Rat
		want   int64
	}{
		{"half", 3_000, big.NewRat(1, 2), 1_500},
		{"third of 3000", 3_000, big.NewRat(1, 3), 1_000},
		// 1000 * 1/3 = 333.33... → 切り捨てで 333。事業者ではなく利用者に有利な側へ倒す。
		{"third of 1000", 1_000, big.NewRat(1, 3), 333},
		{"two thirds", 1_000, big.NewRat(2, 3), 666},
		{"odd half", 999, big.NewRat(1, 2), 499},
		{"zero", 1_000, new(big.Rat), 0},
		{"whole", 1_000, big.NewRat(1, 1), 1_000},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := domain.JPY(tt.amount).Scale(tt.ratio)
			if err != nil {
				t.Fatalf("Scale: %v", err)
			}
			if got.Amount != tt.want {
				t.Errorf("Scale(%s) = %d, want %d", tt.ratio, got.Amount, tt.want)
			}
		})
	}
}

// 負の金額でも floor で丸める。Python 側の // と結果を揃えるための取り決め。
// 0 方向への切り捨て（Quo）だと -333 になり、言語ごとに違う金額が出る。
func TestMoneyScaleOfANegativeAmountFloors(t *testing.T) {
	tests := []struct {
		name   string
		amount int64
		ratio  *big.Rat
		want   int64
	}{
		{"third", -1_000, big.NewRat(1, 3), -334},
		{"two thirds", -1_000, big.NewRat(2, 3), -667},
		{"odd half", -999, big.NewRat(1, 2), -500},
		{"clean half", -3_000, big.NewRat(1, 2), -1_500},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := domain.JPY(tt.amount).Scale(tt.ratio)
			if err != nil {
				t.Fatalf("Scale: %v", err)
			}
			if got.Amount != tt.want {
				t.Errorf("Scale(%d, %s) = %d, want %d", tt.amount, tt.ratio, got.Amount, tt.want)
			}
		})
	}
}

func TestMoneyScaleRejectsNegativeRatio(t *testing.T) {
	if _, err := domain.JPY(1_000).Scale(big.NewRat(-1, 2)); !errors.Is(err, domain.ErrInvariantViolation) {
		t.Errorf("Scale(-1/2) = %v, want ErrInvariantViolation", err)
	}
}

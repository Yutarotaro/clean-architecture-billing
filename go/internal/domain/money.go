package domain

import (
	"fmt"
	"math/big"
)

// Money は通貨の最小単位を整数で保持する値オブジェクト。
//
// 金額に浮動小数点を使わない。丸め誤差はそのまま会計上の差異になる。
// 日本円なら 1 円、米ドルならセントを Amount に入れる。
type Money struct {
	Amount   int64
	Currency string
}

// NewMoney は金額を作る。通貨コードは ISO 4217 の大文字 3 文字であること。
func NewMoney(amount int64, currency string) (Money, error) {
	if len(currency) != 3 {
		return Money{}, Invalid("currency must be a 3-letter ISO 4217 code, got %q", currency)
	}
	for _, r := range currency {
		if r < 'A' || r > 'Z' {
			return Money{}, Invalid("currency must be upper-case, got %q", currency)
		}
	}
	return Money{Amount: amount, Currency: currency}, nil
}

// JPY は日本円の金額を作る。テストとサンプルの記述を短くするためのもの。
func JPY(amount int64) Money {
	return Money{Amount: amount, Currency: "JPY"}
}

// Zero は同じ通貨のゼロを返す。
func (m Money) Zero() Money {
	return Money{Amount: 0, Currency: m.Currency}
}

func (m Money) IsZero() bool     { return m.Amount == 0 }
func (m Money) IsPositive() bool { return m.Amount > 0 }
func (m Money) IsNegative() bool { return m.Amount < 0 }

// Add は加算する。通貨が異なればエラーを返す。
//
// error を返すのは冗長に見えるが、通貨違いを黙って許すと「1000 円 + 10 ドル = 1010」
// という値がそのまま請求書に載る。ここは静かに間違ってはいけない場所である。
func (m Money) Add(other Money) (Money, error) {
	if err := m.assertSameCurrency(other); err != nil {
		return Money{}, err
	}
	return Money{Amount: m.Amount + other.Amount, Currency: m.Currency}, nil
}

// Sub は減算する。
func (m Money) Sub(other Money) (Money, error) {
	if err := m.assertSameCurrency(other); err != nil {
		return Money{}, err
	}
	return Money{Amount: m.Amount - other.Amount, Currency: m.Currency}, nil
}

// Neg は符号を反転する。
func (m Money) Neg() Money {
	return Money{Amount: -m.Amount, Currency: m.Currency}
}

// Scale は比率を掛けて最小単位に丸める。
//
// 丸めは切り捨てで統一する。日割りでは事業者ではなく利用者に有利な方向へ倒す、という
// 方針をここ 1 箇所に閉じ込めている。big.Rat を使うのは、比率を float64 にした時点で
// 「なぜか 1 円ずれる」たぐいのバグが入り込むため。
func (m Money) Scale(ratio *big.Rat) (Money, error) {
	if ratio.Sign() < 0 {
		return Money{}, Invalid("ratio must not be negative, got %s", ratio.String())
	}
	scaled := new(big.Rat).Mul(new(big.Rat).SetInt64(m.Amount), ratio)
	// Quo（0 方向への切り捨て）ではなく Div（floor）を使う。分母は常に正なので
	// Div は floor と一致する。負の金額で Quo を使うと Python 側の // と結果が
	// 割れる（-1000 * 1/3 が Quo では -333、floor では -334）。同じ入力に対して
	// 言語ごとに違う金額が出るのは、それだけで不具合である。
	floored := new(big.Int).Div(scaled.Num(), scaled.Denom())
	return Money{Amount: floored.Int64(), Currency: m.Currency}, nil
}

func (m Money) String() string {
	return fmt.Sprintf("%d %s", m.Amount, m.Currency)
}

func (m Money) assertSameCurrency(other Money) error {
	if m.Currency != other.Currency {
		return fmt.Errorf("%w: %s != %s", ErrCurrencyMismatch, m.Currency, other.Currency)
	}
	return nil
}

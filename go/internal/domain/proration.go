package domain

import "time"

// Proration はプラン変更時の差額の内訳。
//
// Net だけを返さないのは、請求書に「旧プランの未使用分 -400 円」「新プランの残期間分
// +1,200 円」と明細で出せるようにするため。合計だけでは顧客からの問い合わせに答えられない。
type Proration struct {
	// Credit は旧プランのうち、まだ使っていない分（正の値で保持）。
	Credit Money
	// Charge は新プランの、残り期間ぶんの金額。
	Charge Money
}

// Net は今すぐ請求すべき差額。負なら顧客側にクレジットが残る。
func (p Proration) Net() (Money, error) {
	return p.Charge.Sub(p.Credit)
}

// IsNoop は請求も返金も発生しないことを表す。
func (p Proration) IsNoop() bool {
	return p.Credit.IsZero() && p.Charge.IsZero()
}

// Prorate は期間の残り割合に応じて、旧プランの返金額と新プランの請求額を求める。
//
// どのエンティティにも自然には属さない計算なので、純粋関数として切り出している。
// 入力も出力も値オブジェクトだけで、DB もネットワークも時計も触らない。
func Prorate(period BillingPeriod, at time.Time, oldPrice, newPrice Money) (Proration, error) {
	if oldPrice.Currency != newPrice.Currency {
		return Proration{}, Invalid("cannot prorate across currencies: %s and %s",
			oldPrice.Currency, newPrice.Currency)
	}
	ratio := period.RemainingRatio(at)
	credit, err := oldPrice.Scale(ratio)
	if err != nil {
		return Proration{}, err
	}
	charge, err := newPrice.Scale(ratio)
	if err != nil {
		return Proration{}, err
	}
	return Proration{Credit: credit, Charge: charge}, nil
}

"""日割り計算。

どのエンティティにも自然には属さない計算なので、ドメインサービス（純粋関数）として
切り出している。入力は値オブジェクトだけ、出力も値オブジェクトだけ。DB もネットワーク
も時計も触らないので、テストは表を書くだけで済む。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from billing.domain.money import Money
from billing.domain.period import BillingPeriod


@dataclass(frozen=True, slots=True)
class Proration:
    """プラン変更時の差額の内訳。

    ``net`` だけを返さないのは、請求書に「旧プランの未使用分 -400 円」「新プランの
    残期間分 +1,200 円」と明細で出せるようにするため。合計だけ渡されても、顧客からの
    問い合わせに答えられない。
    """

    credit: Money
    """旧プランのうち、まだ使っていない分。顧客に返す側の金額（正の値で保持）。"""

    charge: Money
    """新プランの、残り期間ぶんの金額。"""

    @property
    def net(self) -> Money:
        """今すぐ請求すべき差額。負なら顧客側にクレジットが残る。"""
        return self.charge - self.credit

    @property
    def is_noop(self) -> bool:
        return self.credit.is_zero and self.charge.is_zero


def prorate(
    *,
    period: BillingPeriod,
    at: datetime,
    old_price: Money,
    new_price: Money,
) -> Proration:
    """期間の残り割合に応じて、旧プランの返金額と新プランの請求額を求める。

    期間の途中でプランを変えたとき、その期間はすでに旧プランの料金で請求済みである
    という前提に立つ。だから「使っていない分を返し、新しい料金で取り直す」。
    """
    ratio = period.remaining_ratio(at)
    return Proration(credit=old_price.scale(ratio), charge=new_price.scale(ratio))

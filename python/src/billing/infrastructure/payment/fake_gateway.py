"""PaymentGateway ポートのテスト用実装。

モックではなく fake である。呼ばれた回数を検証するのではなく、本物と同じ規約
（同じ冪等キーには同じ結果を返す）を実際に実装している。この違いは重要で、モックだと
「二重課金しないこと」をテストしようとしても、モック自身が二重課金を許してしまう。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from billing.application.ports import PaymentResult
from billing.domain.ids import CustomerId
from billing.domain.money import Money


@dataclass(frozen=True, slots=True)
class ChargeAttempt:
    customer_id: CustomerId
    amount: Money
    idempotency_key: str
    description: str


class FakePaymentGateway:
    """既定ではすべて成功する。``decline_when`` を渡すと任意の条件で失敗させられる。"""

    def __init__(self, *, decline_when: Callable[[ChargeAttempt], bool] | None = None) -> None:
        self._decline_when = decline_when
        self.attempts: list[ChargeAttempt] = []
        self._results: dict[str, PaymentResult] = {}

    def charge(
        self,
        *,
        customer_id: CustomerId,
        amount: Money,
        idempotency_key: str,
        description: str,
    ) -> PaymentResult:
        attempt = ChargeAttempt(
            customer_id=customer_id,
            amount=amount,
            idempotency_key=idempotency_key,
            description=description,
        )
        self.attempts.append(attempt)

        cached = self._results.get(idempotency_key)
        if cached is not None:
            # 本物の決済代行と同じ振る舞い。同じ鍵で二度目が来ても、新しく課金せずに
            # 一度目の結果をそのまま返す。
            return cached

        if self._decline_when is not None and self._decline_when(attempt):
            result = PaymentResult(succeeded=False, failure_reason="card_declined")
        else:
            result = PaymentResult(
                succeeded=True, provider_reference=f"ch_{len(self._results) + 1}"
            )
        self._results[idempotency_key] = result
        return result

    @property
    def settled_amount(self) -> int:
        """実際に課金された合計（冪等キーで重複排除したあと）。"""
        return sum(
            attempt.amount.amount
            for key, attempt in _first_by_key(self.attempts)
            if self._results[key].succeeded
        )


def _first_by_key(attempts: list[ChargeAttempt]) -> list[tuple[str, ChargeAttempt]]:
    seen: dict[str, ChargeAttempt] = {}
    for attempt in attempts:
        seen.setdefault(attempt.idempotency_key, attempt)
    return list(seen.items())

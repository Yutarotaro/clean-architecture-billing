"""PaymentGateway ポートのテスト用実装。

モックではなく fake である。呼ばれた回数を検証するのではなく、本物と同じ規約
（同じ冪等キーには同じ結果を返す／同じ鍵で違うパラメータが来たら拒む）を実際に
実装している。この違いは重要で、モックだと「二重課金しないこと」をテストしようと
しても、モック自身が二重課金を許してしまう。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from billing.application.ports import PaymentGatewayError, PaymentResult
from billing.domain.ids import CustomerId
from billing.domain.money import Money


@dataclass(frozen=True, slots=True)
class ChargeAttempt:
    customer_id: CustomerId
    amount: Money
    idempotency_key: str
    description: str


@dataclass(frozen=True, slots=True)
class _Settled:
    """一度決着した課金。冪等キーごとに 1 つだけ残る。"""

    attempt: ChargeAttempt
    result: PaymentResult


class FakePaymentGateway:
    """既定ではすべて成功する。

    ``decline_when`` は「カードが拒否された」を、``fail_when`` は「通信できず結果が
    分からない」を再現する。本物の決済代行でも両者の扱いはまったく違い、前者は
    未払いとして猶予期間に入れてよいが、後者は課金が成功している可能性がある。
    """

    def __init__(
        self,
        *,
        decline_when: Callable[[ChargeAttempt], bool] | None = None,
        fail_when: Callable[[ChargeAttempt], bool] | None = None,
    ) -> None:
        self._decline_when = decline_when
        self._fail_when = fail_when
        self.attempts: list[ChargeAttempt] = []
        self._settled: dict[str, _Settled] = {}

    def set_decline(self, decline_when: Callable[[ChargeAttempt], bool] | None) -> None:
        """拒否する条件を差し替える。テストの途中で挙動を変えるために使う。"""
        self._decline_when = decline_when

    def set_fail(self, fail_when: Callable[[ChargeAttempt], bool] | None) -> None:
        """通信失敗させる条件を差し替える。"""
        self._fail_when = fail_when

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

        if self._fail_when is not None and self._fail_when(attempt):
            raise PaymentGatewayError(f"gateway unreachable for {idempotency_key!r}")

        settled = self._settled.get(idempotency_key)
        if settled is not None:
            if (
                settled.attempt.amount != attempt.amount
                or settled.attempt.customer_id != attempt.customer_id
            ):
                # 本物の決済代行は、同じ鍵に違うパラメータが来たら専用のエラーを返す。
                # ここを素通しにすると「同じ鍵で違う金額を請求してしまう」たぐいの
                # 不具合がテストで検出できなくなる。冪等性を検証するための fake が、
                # 冪等性の誤用を隠すことになる。
                raise PaymentGatewayError(
                    f"idempotency key {idempotency_key!r} was already used with "
                    f"different parameters (was {settled.attempt.amount}, now {attempt.amount})"
                )
            # 同じ鍵で二度目が来ても、新しく課金せずに一度目の結果をそのまま返す。
            return settled.result

        if self._decline_when is not None and self._decline_when(attempt):
            result = PaymentResult(succeeded=False, failure_reason="card_declined")
        else:
            result = PaymentResult(
                succeeded=True, provider_reference=f"ch_{len(self._settled) + 1}"
            )
        self._settled[idempotency_key] = _Settled(attempt=attempt, result=result)
        return result

    @property
    def settled_amount(self) -> int:
        """実際に課金された合計（冪等キーで重複排除したあと）。"""
        return sum(
            settled.attempt.amount.amount
            for settled in self._settled.values()
            if settled.result.succeeded
        )

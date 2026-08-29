"""外部システムとの境界。

リポジトリ（domain/repositories.py）が「集約の永続化」を表すのに対して、ここは
「アプリケーションが仕事を進めるために必要な、ドメインの語彙ではないもの」を置く。
時計、決済代行、ID 採番。どれもテストでは差し替えたいものばかりである。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self

from billing.domain.ids import CustomerId
from billing.domain.money import Money
from billing.domain.repositories import (
    InvoiceRepository,
    PlanRepository,
    SubscriptionRepository,
)


class Clock(Protocol):
    """現在時刻の供給源。

    ``datetime.now()`` を直接呼ぶコードはテストできない。「猶予期間を 14 日過ぎたら
    解約される」を検証するのに、14 日待つわけにはいかない。時刻を注入可能にすると、
    その手のテストが数ミリ秒で書ける。
    """

    def now(self) -> datetime:
        """tz-aware な現在時刻（UTC）を返す。"""
        ...


class IdGenerator(Protocol):
    """識別子の採番。

    ドメインが ``uuid4()`` を呼んでしまうと、同じ入力から同じ結果が出なくなる。
    採番も外から与える。
    """

    def new_id(self) -> str: ...


@dataclass(frozen=True, slots=True)
class PaymentResult:
    """決済代行からの応答。"""

    succeeded: bool
    provider_reference: str | None = None
    failure_reason: str | None = None


class PaymentGateway(Protocol):
    """決済代行。Stripe でも PAY.JP でも、この形に合わせて adapter を書く。"""

    def charge(
        self,
        *,
        customer_id: CustomerId,
        amount: Money,
        idempotency_key: str,
        description: str,
    ) -> PaymentResult:
        """``amount`` を請求する。

        ``idempotency_key`` は必須にしてある。ネットワークが不安定なときの再送で
        二重に課金しないための鍵であり、任意にすると必ず渡し忘れる。
        """
        ...


class UnitOfWork(Protocol):
    """トランザクション境界。

    1 つのユースケースが「全部成功したか、全部なかったことになるか」のどちらかで
    終わることを保証する。どのリポジトリが同じトランザクションに参加するかを、
    ここで束ねて表現している。
    """

    # 読み取り専用プロパティとして宣言している。Protocol の「代入できる属性」は
    # 型として不変（invariant）に扱われるため、``SqlSubscriptionRepository`` を持つ
    # 実装が ``SubscriptionRepository`` を要求するこの Protocol を満たせなくなる。
    # 読み取り専用にすると共変になり、具象リポジトリを持つ実装がそのまま通る。
    # 実装側は普通の属性で構わない。
    @property
    def subscriptions(self) -> SubscriptionRepository: ...

    @property
    def invoices(self) -> InvoiceRepository: ...

    @property
    def plans(self) -> PlanRepository: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """例外が起きていれば rollback する。commit は明示的に呼ばせる。

        「例外が出なければ自動 commit」にしないのは、途中で return したときに
        意図せず commit される事故を防ぐため。commit は必ず目に見える形で書く。
        """
        ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

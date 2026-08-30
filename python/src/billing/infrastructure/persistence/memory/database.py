"""プロセス内に置く「データベース」。"""

from __future__ import annotations

from dataclasses import dataclass, field

from billing.domain.ids import InvoiceId, PlanId, SubscriptionId
from billing.domain.invoice import Invoice
from billing.domain.plan import Plan
from billing.domain.subscription import Subscription


@dataclass
class MemoryDatabase:
    """コミット済みの状態。"""

    plans: dict[PlanId, Plan] = field(default_factory=dict)
    subscriptions: dict[SubscriptionId, Subscription] = field(default_factory=dict)
    invoices: dict[InvoiceId, Invoice] = field(default_factory=dict)
    #: 楽観ロック用のバージョン。SQL の version 列、DynamoDB の version 属性に相当する。
    versions: dict[str, int] = field(default_factory=dict)


@dataclass
class Staging:
    """1 つの UnitOfWork の中で溜めた変更。

    commit されるまで ``MemoryDatabase`` には触れない。読み取りはまずここを見るので、
    同じトランザクションの中では自分の書き込みが見える（read-your-writes）。

    「作業用にデータベース全体をコピーし、commit で丸ごと置き換える」方式にしては
    いけない。楽観ロックは触った集約のバージョンしか見ないので、**自分が触っていない
    集約への他トランザクションの更新が、検査を素通りして消える**。
    """

    plans: dict[PlanId, Plan] = field(default_factory=dict)
    subscriptions: dict[SubscriptionId, Subscription] = field(default_factory=dict)
    invoices: dict[InvoiceId, Invoice] = field(default_factory=dict)
    versions: dict[str, int] = field(default_factory=dict)

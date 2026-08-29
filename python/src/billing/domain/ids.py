"""識別子。

``str`` のまま引き回すと、顧客 ID を渡すべき場所にプラン ID を渡しても型検査が
通ってしまう。NewType にしておけば mypy が取り違えを弾く。実行時のコストはゼロ。
"""

from __future__ import annotations

from typing import NewType

CustomerId = NewType("CustomerId", str)
PlanId = NewType("PlanId", str)
SubscriptionId = NewType("SubscriptionId", str)
InvoiceId = NewType("InvoiceId", str)
IdempotencyKey = NewType("IdempotencyKey", str)

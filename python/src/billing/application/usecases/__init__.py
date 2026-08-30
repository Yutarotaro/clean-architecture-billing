"""ユースケース 1 つにつき 1 クラス。

依存はすべてコンストラクタで受け取る。クラスの外から見えるのは ``execute`` 1 つだけ
にしてあり、「このユースケースは何ができるのか」が型シグネチャだけで読み取れる。
"""

from billing.application.usecases.cancel_subscription import CancelSubscription
from billing.application.usecases.change_plan import ChangePlan
from billing.application.usecases.record_payment_result import RecordPaymentResult
from billing.application.usecases.renew_due_subscriptions import RenewDueSubscriptions
from billing.application.usecases.settle_unpaid_invoices import SettleUnpaidInvoices
from billing.application.usecases.subscribe_to_plan import SubscribeToPlan

__all__ = [
    "CancelSubscription",
    "ChangePlan",
    "RecordPaymentResult",
    "RenewDueSubscriptions",
    "SettleUnpaidInvoices",
    "SubscribeToPlan",
]

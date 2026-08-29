"""解約のユースケース。"""

from __future__ import annotations

from billing.application.charging import UnitOfWorkFactory
from billing.application.dto import CancelCommand, SubscriptionView
from billing.application.errors import EntityNotFound
from billing.application.ports import Clock
from billing.domain.ids import SubscriptionId


class CancelSubscription:
    """解約する。既定は期末解約で、``immediately`` を立てると即時解約になる。

    外部システムを一切叩かないので、トランザクションは 1 つで済む。ユースケースが
    全部同じ形をしている必要はない。必要な分だけ書く。
    """

    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def execute(self, command: CancelCommand) -> SubscriptionView:
        with self._uow_factory() as uow:
            subscription = uow.subscriptions.get(SubscriptionId(command.subscription_id))
            if subscription is None:
                raise EntityNotFound("subscription", command.subscription_id)

            subscription.cancel(at=self._clock.now(), immediately=command.immediately)
            uow.subscriptions.save(subscription)
            uow.commit()
            return SubscriptionView.of(subscription)

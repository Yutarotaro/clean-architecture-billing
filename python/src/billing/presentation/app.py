"""FastAPI アプリケーションの組み立て。"""

from __future__ import annotations

from fastapi import FastAPI

from billing.presentation.api.errors import install_error_handlers
from billing.presentation.api.routers import admin, invoices, plans, subscriptions, webhooks
from billing.presentation.container import Container, build_container
from billing.presentation.settings import Settings

DESCRIPTION = """\
クリーンアーキテクチャで構成したサブスクリプション課金 API のサンプル。

同じユースケース層が、インメモリ・SQLite・DynamoDB の 3 つの永続化実装の上で
動く。切り替えは環境変数 `BILLING_PERSISTENCE` だけで行われ、
`application/` 以下のコードは 1 行も変わらない。
"""


def create_app(settings: Settings | None = None, *, container: Container | None = None) -> FastAPI:
    """アプリケーションを作る。

    ``container`` を差し替えられるようにしてあるのはテストのためである。時計と
    決済代行だけを置き換えて、それ以外は本番とまったく同じ配線で API を叩ける。
    """
    settings = settings or Settings.from_env()
    app = FastAPI(
        title="Billing API",
        description=DESCRIPTION,
        version="0.1.0",
    )
    app.state.container = container or build_container(settings)

    install_error_handlers(app)
    app.include_router(plans.router)
    app.include_router(subscriptions.router)
    app.include_router(invoices.router)
    app.include_router(webhooks.router)
    app.include_router(admin.router)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "persistence": str(settings.persistence)}

    return app

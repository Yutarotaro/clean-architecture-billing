"""例外を HTTP ステータスに翻訳する。

この対応表がプレゼンテーション層にあることが重要である。``IllegalTransition`` を
送出したドメイン層は 409 という数字を知らないし、知る必要もない。同じ例外を
gRPC で返すなら FAILED_PRECONDITION に、CLI なら終了コード 3 に、それぞれの
境界で好きに訳せばよい。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from billing.application.errors import (
    ConcurrencyConflict,
    ConflictingRequest,
    EntityNotFound,
)
from billing.application.ports import PaymentGatewayError
from billing.domain.errors import DomainError, IllegalTransition


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(EntityNotFound)
    async def _not_found(_: Request, exc: EntityNotFound) -> JSONResponse:
        return _error(404, "not_found", str(exc))

    @app.exception_handler(IllegalTransition)
    async def _illegal_transition(_: Request, exc: IllegalTransition) -> JSONResponse:
        # 「解約済みの契約は変更できない」は入力の誤りではなく、資源の現在の状態と
        # 要求が両立しないということ。422 ではなく 409 が正しい。
        return _error(409, "illegal_state", str(exc))

    @app.exception_handler(ConcurrencyConflict)
    async def _concurrency(_: Request, exc: ConcurrencyConflict) -> JSONResponse:
        return _error(409, "concurrent_modification", str(exc))

    @app.exception_handler(ConflictingRequest)
    async def _conflicting(_: Request, exc: ConflictingRequest) -> JSONResponse:
        return _error(409, "conflicting_request", str(exc))

    @app.exception_handler(PaymentGatewayError)
    async def _payment_gateway(_: Request, exc: PaymentGatewayError) -> JSONResponse:
        # 決済代行に届かなかった。こちらの落ち度ではないので 500 ではなく 502。
        # 課金できたかどうかは分からないため、請求書は open のまま残っており、
        # SettleUnpaidInvoices が後から拾い直す。
        return _error(502, "payment_gateway_unreachable", str(exc))

    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        # より具体的なハンドラ（IllegalTransition）が先に照合されるため、ここに
        # 来るのは不変条件違反だけになる。
        return _error(422, "domain_rule_violated", str(exc))


def _error(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "detail": detail})

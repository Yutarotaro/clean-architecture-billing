"""請求書の参照。"""

from __future__ import annotations

from fastapi import APIRouter

from billing.presentation.api.deps import ContainerDep
from billing.presentation.api.schemas import InvoiceResponse

router = APIRouter(prefix="/customers", tags=["invoices"])


@router.get("/{customer_id}/invoices", summary="顧客の請求書一覧")
def list_invoices(container: ContainerDep, customer_id: str) -> list[InvoiceResponse]:
    return [InvoiceResponse.of(view) for view in container.queries.list_invoices(customer_id)]

"""プラン一覧。"""

from __future__ import annotations

from fastapi import APIRouter

from billing.presentation.api.deps import ContainerDep
from billing.presentation.api.schemas import PlanResponse

router = APIRouter(prefix="/plans", tags=["plans"])


@router.get("", summary="契約できるプランの一覧")
def list_plans(container: ContainerDep) -> list[PlanResponse]:
    return [PlanResponse.of(view) for view in container.queries.list_plans()]

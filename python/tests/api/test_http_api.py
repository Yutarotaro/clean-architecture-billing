"""HTTP 境界のテスト。

ここで確かめるのは「HTTP という形式との対応づけ」であって、ビジネスルールではない。
日割りが正しいかはユースケース層のテストで済んでいるので、この層では
「ドメインの例外が正しいステータスコードに翻訳されるか」「必須ヘッダが効くか」
といった、この層にしかない関心だけを見る。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from billing.infrastructure.clock import FixedClock
from billing.presentation.app import create_app
from billing.presentation.container import Container
from billing.presentation.settings import Settings


@pytest.fixture
def client(container: Container) -> Iterator[TestClient]:
    # 本番と同じ create_app を通す。差し替えるのはコンテナだけ。
    app = create_app(Settings(seed_plans=False), container=container)
    with TestClient(app) as test_client:
        yield test_client


def subscribe(client: TestClient, plan_id: str = "basic") -> dict[str, Any]:
    response = client.post("/subscriptions", json={"customer_id": "cus-1", "plan_id": plan_id})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def test_health(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"


def test_list_plans(client: TestClient) -> None:
    response = client.get("/plans")

    assert response.status_code == 200
    assert [plan["id"] for plan in response.json()] == ["basic", "pro", "pro-yearly"]
    assert response.json()[0]["price"] == {"amount": 1_000, "currency": "JPY"}


def test_subscribe_returns_201_with_the_first_invoice(client: TestClient) -> None:
    body = subscribe(client, "pro")

    assert body["subscription"]["status"] == "active"
    assert body["invoice"]["total"] == {"amount": 3_000, "currency": "JPY"}
    assert body["payment_failed"] is False


def test_get_subscription(client: TestClient) -> None:
    subscription_id = subscribe(client)["subscription"]["id"]

    response = client.get(f"/subscriptions/{subscription_id}")

    assert response.status_code == 200
    assert response.json()["plan_id"] == "basic"


def test_unknown_subscription_is_404(client: TestClient) -> None:
    response = client.get("/subscriptions/nope")

    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_change_plan_requires_an_idempotency_key(client: TestClient) -> None:
    """鍵を任意にすると、いつか誰かが渡し忘れて二重課金になる。"""
    subscription_id = subscribe(client)["subscription"]["id"]

    response = client.post(
        f"/subscriptions/{subscription_id}/plan-changes", json={"new_plan_id": "pro"}
    )

    assert response.status_code == 422


def test_change_plan(client: TestClient, clock: FixedClock) -> None:
    subscription_id = subscribe(client)["subscription"]["id"]
    clock.set(datetime(2026, 1, 16, 12, tzinfo=UTC))

    response = client.post(
        f"/subscriptions/{subscription_id}/plan-changes",
        json={"new_plan_id": "pro"},
        headers={"Idempotency-Key": "change-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["proration"]["net"] == {"amount": 1_000, "currency": "JPY"}
    assert body["invoice"]["status"] == "paid"


def test_changing_the_plan_of_a_canceled_subscription_is_409(client: TestClient) -> None:
    """状態と要求が両立しない。入力の誤り（422）ではなく競合（409）。"""
    subscription_id = subscribe(client)["subscription"]["id"]
    client.post(f"/subscriptions/{subscription_id}/cancellation", json={"immediately": True})

    response = client.post(
        f"/subscriptions/{subscription_id}/plan-changes",
        json={"new_plan_id": "pro"},
        headers={"Idempotency-Key": "change-1"},
    )

    assert response.status_code == 409
    assert response.json()["error"] == "illegal_state"


def test_changing_to_the_same_plan_is_422(client: TestClient) -> None:
    """不変条件の違反。状態は正常なので 409 ではなく 422。"""
    subscription_id = subscribe(client)["subscription"]["id"]

    response = client.post(
        f"/subscriptions/{subscription_id}/plan-changes",
        json={"new_plan_id": "basic"},
        headers={"Idempotency-Key": "change-1"},
    )

    assert response.status_code == 422
    assert response.json()["error"] == "domain_rule_violated"


def test_cancel_defaults_to_end_of_period(client: TestClient) -> None:
    subscription_id = subscribe(client)["subscription"]["id"]

    response = client.post(f"/subscriptions/{subscription_id}/cancellation", json={})

    assert response.status_code == 200
    assert response.json()["status"] == "active"
    assert response.json()["cancel_at_period_end"] is True


def test_list_invoices_for_a_customer(client: TestClient) -> None:
    subscribe(client)

    response = client.get("/customers/cus-1/invoices")

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["status"] == "paid"


def test_payment_webhook(client: TestClient) -> None:
    invoice_id = subscribe(client)["invoice"]["id"]

    response = client.post(
        "/webhooks/payments",
        json={"invoice_id": invoice_id, "succeeded": True, "provider_reference": "ch_1"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "paid"


def test_admin_renewals(client: TestClient, clock: FixedClock) -> None:
    subscribe(client)
    clock.set(datetime(2026, 2, 1, tzinfo=UTC))

    response = client.post("/admin/renewals")

    assert response.status_code == 200
    assert response.json() == {
        "renewed": 1,
        "invoiced": 1,
        "payment_failed": 0,
        "terminated": 0,
        "canceled_for_nonpayment": 0,
    }


def test_openapi_is_generated(client: TestClient) -> None:
    """OpenAPI が壊れていないこと。docs/ からこの定義を公開している。"""
    spec = client.get("/openapi.json").json()

    assert spec["info"]["title"] == "Billing API"
    assert "/subscriptions/{subscription_id}/plan-changes" in spec["paths"]
